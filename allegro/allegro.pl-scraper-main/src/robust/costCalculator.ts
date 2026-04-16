/**
 * Precise per-task cost calculator.
 *
 * Called after every strategy execution to compute an itemized cost breakdown.
 * The Python backend receives total_cost_usd and cost_breakdown in every task
 * result and uses them for:
 *   - stop-loss threshold (stoploss_max_cost_per_1000)
 *   - billing / usage_records
 *   - Prometheus metrics (jj_cost_per_1000_ean_avg)
 *
 * Cost model (April 2026):
 *   Proxy:      billed per GB (residential ~$2.50, mobile ~$12, sticky ~$3.50)
 *   CAPTCHA:    billed per solve (DataDome ~$0.003, ReCAPTCHA ~$0.002)
 *   Browser:    compute time (Playwright / Chromium headless ~$0.15/h)
 *   Antidetect: per-minute license (Kameleo ~$0.008/min, Octo ~$0.006/min)
 */

import { robustConfig } from './config';
import type {
    CostBreakdown,
    ProxyType,
    AntidetectTool,
    RobustMetadata,
    StrategyName,
    FallbackLevel,
} from './types';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface CostInput {
    /** Which strategy produced the result */
    strategy: StrategyName;
    fallback_level: FallbackLevel;
    /** Type of proxy used */
    proxy_type: ProxyType;
    /** Antidetect tool used (null for raw / plain Playwright) */
    antidetect_tool: AntidetectTool;
    /** Number of CAPTCHA solves (DataDome + ReCAPTCHA combined) */
    captcha_solves: number;
    /** How many of those were DataDome (vs ReCAPTCHA) */
    datadome_solves: number;
    /** Browser runtime in milliseconds (0 for raw HTTP strategy) */
    browser_runtime_ms: number;
    /** Estimated request payload size in KB (null = use default) */
    estimated_kb: number | null;
}

export interface CostResult {
    cost_breakdown: CostBreakdown;
    total_cost_usd: number;
}

/**
 * Calculate the cost of a single task execution.
 *
 * Every field in the returned CostBreakdown maps directly to a line item
 * that the Python backend can persist and aggregate.
 */
export function calculateTaskCost(input: CostInput): CostResult {
    const rates = robustConfig.COST_RATES;

    // ---- Proxy cost (traffic-based) ----
    const estimatedKb = input.estimated_kb
        ?? (input.strategy === 'raw'
            ? rates.estimated_kb_per_request_raw
            : rates.estimated_kb_per_request_browser);
    const estimatedGb = estimatedKb / (1024 * 1024); // KB -> GB

    let proxyRatePerGb: number;
    switch (input.proxy_type) {
        case 'mobile':
            proxyRatePerGb = rates.proxy_mobile_per_gb_usd;
            break;
        case 'sticky':
            proxyRatePerGb = rates.proxy_sticky_per_gb_usd;
            break;
        case 'datacenter':
            proxyRatePerGb = rates.proxy_datacenter_per_gb_usd;
            break;
        case 'residential':
        default:
            proxyRatePerGb = rates.proxy_residential_per_gb_usd;
            break;
    }
    const proxyCost = estimatedGb * proxyRatePerGb;

    // ---- CAPTCHA cost (per-solve, different rates) ----
    const datadomeSolves = Math.min(input.datadome_solves, input.captcha_solves);
    const recaptchaSolves = Math.max(0, input.captcha_solves - datadomeSolves);
    const captchaCost =
        datadomeSolves * rates.captcha_datadome_per_solve_usd +
        recaptchaSolves * rates.captcha_recaptcha_per_solve_usd;

    // ---- Browser runtime cost ----
    const browserHours = input.browser_runtime_ms / (1000 * 3600);
    const browserCost = browserHours * rates.browser_runtime_per_hour_usd;

    // ---- Antidetect tool cost ----
    let antidetectCost = 0;
    if (input.antidetect_tool && input.browser_runtime_ms > 0) {
        const minutes = input.browser_runtime_ms / (1000 * 60);
        switch (input.antidetect_tool) {
            case 'kameleo':
                antidetectCost = minutes * rates.antidetect_kameleo_per_min_usd;
                break;
            case 'camoufox':
                antidetectCost = minutes * rates.antidetect_camoufox_per_min_usd;
                break;
            case 'octo':
                antidetectCost = minutes * rates.antidetect_octo_per_min_usd;
                break;
            case 'gologin':
                antidetectCost = minutes * rates.antidetect_gologin_per_min_usd;
                break;
        }
    }

    // ---- Total ----
    const totalCost = proxyCost + captchaCost + browserCost + antidetectCost;

    // ---- Notes (human-readable summary) ----
    const parts: string[] = [];
    if (proxyCost > 0) parts.push(`proxy(${input.proxy_type}): $${proxyCost.toFixed(6)}`);
    if (captchaCost > 0) parts.push(`captcha(${input.captcha_solves}x): $${captchaCost.toFixed(6)}`);
    if (browserCost > 0) parts.push(`browser(${Math.round(input.browser_runtime_ms)}ms): $${browserCost.toFixed(6)}`);
    if (antidetectCost > 0) parts.push(`${input.antidetect_tool}: $${antidetectCost.toFixed(6)}`);

    return {
        cost_breakdown: {
            proxy_cost_usd: round6(proxyCost),
            captcha_cost_usd: round6(captchaCost),
            browser_runtime_cost_usd: round6(browserCost),
            antidetect_tool_cost_usd: round6(antidetectCost),
            notes: parts.join(', ') || 'no cost',
        },
        total_cost_usd: round6(totalCost),
    };
}

/**
 * Attach cost data to a RobustMetadata object in-place.
 * Convenience wrapper used by strategies after fetching.
 */
export function attachCost(meta: RobustMetadata, input: Omit<CostInput, 'strategy' | 'fallback_level' | 'proxy_type' | 'antidetect_tool'>): void {
    const { cost_breakdown, total_cost_usd } = calculateTaskCost({
        strategy: meta.strategy,
        fallback_level: meta.fallback_level,
        proxy_type: meta.proxy_type,
        antidetect_tool: meta.antidetect_tool,
        ...input,
    });
    meta.cost_breakdown = cost_breakdown;
    meta.total_cost_usd = total_cost_usd;
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function round6(n: number): number {
    return Math.round(n * 1_000_000) / 1_000_000;
}
