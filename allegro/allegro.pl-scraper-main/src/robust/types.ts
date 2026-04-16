/**
 * Unified types for the robust multi-strategy fallback system.
 *
 * Every strategy (raw, stealthPlaywright, antidetectBrowser, mobileFallback)
 * MUST return results conforming to RobustFetchResult so that the Python
 * backend can process all of them identically through the same pipeline
 * (metering, cost calculation, stop-loss, circuit breaker).
 */

import type { AllegroFetchResult } from '@/scraper/allegro';

// ---------------------------------------------------------------------------
// Strategy identifiers
// ---------------------------------------------------------------------------

export type StrategyName =
    | 'raw'
    | 'stealthPlaywright'
    | 'antidetectBrowser'
    | 'mobileFallback';

export type FallbackLevel = 0 | 1 | 2 | 3;

export type ProxyType = 'residential' | 'mobile' | 'sticky' | 'datacenter';

export type AntidetectTool = 'kameleo' | 'camoufox' | 'octo' | 'gologin' | null;

// ---------------------------------------------------------------------------
// Cost breakdown returned with every task result
// ---------------------------------------------------------------------------

export interface CostBreakdown {
    /** Proxy cost in USD for this request */
    proxy_cost_usd: number;
    /** CAPTCHA solving cost in USD (AnySolver / CapSolver) */
    captcha_cost_usd: number;
    /** Browser runtime cost in USD (Playwright/antidetect compute time) */
    browser_runtime_cost_usd: number;
    /** Antidetect tool license cost in USD (per-profile or per-minute) */
    antidetect_tool_cost_usd: number;
    /** Human-readable summary */
    notes: string;
}

// ---------------------------------------------------------------------------
// Extended result: raw AllegroFetchResult + robust metadata
// ---------------------------------------------------------------------------

export interface RobustMetadata {
    /** Which strategy produced this result */
    strategy: StrategyName;
    /** 0 = raw, 1 = stealthPlaywright, 2 = antidetectBrowser, 3 = mobileFallback */
    fallback_level: FallbackLevel;
    /** Type of proxy used for the winning request */
    proxy_type: ProxyType;
    /** Antidetect tool used (null for raw / plain Playwright) */
    antidetect_tool: AntidetectTool;
    /** Sticky session identifier (proxy session ID or browser profile ID) */
    session_id: string | null;
    /** Itemized cost breakdown */
    cost_breakdown: CostBreakdown;
    /** Total cost in USD for this single task */
    total_cost_usd: number;
    /** Browser runtime in milliseconds (0 for raw HTTP) */
    browser_runtime_ms: number;
    /** Which fallback levels were attempted before success */
    attempted_levels: FallbackLevel[];
    /** Error messages from each failed level (for diagnostics) */
    level_errors: Record<number, string>;
}

/**
 * The final result type returned by every strategy and by the fallback chain.
 * It is a superset of AllegroFetchResult (which the existing worker already
 * knows how to handle) plus RobustMetadata (new fields).
 *
 * The API route strips `html` before sending to the Python backend, but keeps
 * all metadata fields so the backend can persist them.
 */
export type RobustFetchResult = AllegroFetchResult & RobustMetadata;

// ---------------------------------------------------------------------------
// Strategy interface - every fallback level implements this
// ---------------------------------------------------------------------------

export interface FetchStrategy {
    /** Human-readable name for logging */
    readonly name: StrategyName;
    /** Fallback level number */
    readonly level: FallbackLevel;
    /**
     * Attempt to fetch Allegro listing for a given EAN.
     * Must throw on failure so the chain can try the next level.
     */
    fetch(ean: string, sessionHint?: string): Promise<RobustFetchResult>;
    /**
     * Clean up browser contexts, profiles, etc.  Called on shutdown or
     * when the chain decides to rotate the strategy instance.
     */
    destroy(): Promise<void>;
    /**
     * Quick health check - is this strategy ready to accept work?
     * (e.g. is the antidetect API reachable, is Playwright installed)
     */
    isAvailable(): Promise<boolean>;
}

// ---------------------------------------------------------------------------
// Helpers to create default metadata
// ---------------------------------------------------------------------------

export function defaultMetadata(strategy: StrategyName, level: FallbackLevel): RobustMetadata {
    return {
        strategy,
        fallback_level: level,
        proxy_type: 'residential',
        antidetect_tool: null,
        session_id: null,
        cost_breakdown: {
            proxy_cost_usd: 0,
            captcha_cost_usd: 0,
            browser_runtime_cost_usd: 0,
            antidetect_tool_cost_usd: 0,
            notes: '',
        },
        total_cost_usd: 0,
        browser_runtime_ms: 0,
        attempted_levels: [level],
        level_errors: {},
    };
}
