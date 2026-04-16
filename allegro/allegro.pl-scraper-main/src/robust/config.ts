/**
 * Configuration for the robust multi-strategy fallback system.
 *
 * All values can be overridden via environment variables. Sensible defaults
 * are provided for a typical DC server + residential proxy setup.
 */

import type { StrategyName, AntidetectTool } from './types';

// ---------------------------------------------------------------------------
// Helpers (duplicated from main config to keep this module self-contained)
// ---------------------------------------------------------------------------

const int = (key: string, fallback: number): number => {
    const v = process.env[key];
    return v ? parseInt(v, 10) : fallback;
};

const float = (key: string, fallback: number): number => {
    const v = process.env[key];
    return v ? parseFloat(v) : fallback;
};

const bool = (key: string, fallback: boolean): boolean => {
    const v = process.env[key];
    if (!v) return fallback;
    return v === 'true' || v === '1';
};

const opt = (key: string, fallback = ''): string => process.env[key] ?? fallback;

// ---------------------------------------------------------------------------
// Fallback level definitions
// ---------------------------------------------------------------------------

export interface FallbackLevelConfig {
    name: StrategyName;
    enabled: boolean;
    maxConcurrency: number;
}

function parseFallbackLevels(): FallbackLevelConfig[] {
    // Default chain: raw -> stealthPlaywright -> antidetectBrowser -> mobileFallback
    // Each can be disabled individually
    return [
        {
            name: 'raw',
            enabled: true, // always enabled, it's the primary
            maxConcurrency: 999, // no limit for raw
        },
        {
            name: 'stealthPlaywright',
            enabled: bool('FALLBACK_LEVEL1_ENABLED', true),
            maxConcurrency: int('FALLBACK_LEVEL1_MAX_CONCURRENCY', 4),
        },
        {
            name: 'antidetectBrowser',
            enabled: bool('FALLBACK_LEVEL2_ENABLED', true),
            maxConcurrency: int('FALLBACK_LEVEL2_MAX_CONCURRENCY', 2),
        },
        {
            name: 'mobileFallback',
            enabled: bool('FALLBACK_LEVEL3_ENABLED', true),
            maxConcurrency: int('FALLBACK_LEVEL3_MAX_CONCURRENCY', 2),
        },
    ];
}

// ---------------------------------------------------------------------------
// Cost rates (USD) - used by costCalculator.ts
// ---------------------------------------------------------------------------

export interface CostRates {
    // Proxy costs per GB of traffic
    proxy_residential_per_gb_usd: number;
    proxy_mobile_per_gb_usd: number;
    proxy_sticky_per_gb_usd: number;
    proxy_datacenter_per_gb_usd: number;

    // CAPTCHA costs per solve
    captcha_datadome_per_solve_usd: number;
    captcha_recaptcha_per_solve_usd: number;

    // Browser runtime cost per hour (compute on DC server)
    browser_runtime_per_hour_usd: number;

    // Antidetect tool costs per profile-minute
    antidetect_kameleo_per_min_usd: number;
    antidetect_camoufox_per_min_usd: number;
    antidetect_octo_per_min_usd: number;
    antidetect_gologin_per_min_usd: number;

    // Estimated bandwidth per request in KB
    estimated_kb_per_request_raw: number;
    estimated_kb_per_request_browser: number;
}

// ---------------------------------------------------------------------------
// Sticky proxy config
// ---------------------------------------------------------------------------

export interface StickyProxyConfig {
    /** Sticky residential proxy endpoint (Decodo / Smartproxy / Oxylabs format) */
    residential_endpoint: string;
    /** Sticky residential session duration in minutes */
    residential_session_ttl_min: number;
    /** Mobile proxy endpoint (4G/LTE) */
    mobile_endpoint: string;
    /** Mobile session duration in minutes */
    mobile_session_ttl_min: number;
    /** Username for proxy auth */
    proxy_username: string;
    /** Password for proxy auth */
    proxy_password: string;
}

// ---------------------------------------------------------------------------
// Antidetect config
// ---------------------------------------------------------------------------

export interface AntidetectConfig {
    /** Which tool to use: kameleo, camoufox, octo, gologin */
    default_tool: AntidetectTool;
    /** Kameleo local API base URL */
    kameleo_api_url: string;
    /** Kameleo API password */
    kameleo_api_password: string;
    /** Camoufox binary path (if using local Camoufox) */
    camoufox_binary_path: string;
    /** Profile reuse: keep browser profile alive for N minutes */
    profile_ttl_min: number;
}

// ---------------------------------------------------------------------------
// Playwright stealth config
// ---------------------------------------------------------------------------

export interface StealthPlaywrightConfig {
    /** Headless mode (true for server, false for debugging) */
    headless: boolean;
    /** Viewport width */
    viewport_width: number;
    /** Viewport height */
    viewport_height: number;
    /** Min delay between actions in ms */
    human_delay_min_ms: number;
    /** Max delay between actions in ms */
    human_delay_max_ms: number;
    /** Number of scroll steps to simulate */
    scroll_steps: number;
    /** Enable canvas/WebGL fingerprint noise */
    canvas_noise: boolean;
    /** Enable ghost-cursor mouse movements */
    ghost_cursor: boolean;
}

// ---------------------------------------------------------------------------
// Main robust config object
// ---------------------------------------------------------------------------

export const robustConfig = Object.freeze({
    /** Master switch for the entire fallback system */
    ENABLE_ROBUST_FALLBACK: bool('ENABLE_ROBUST_FALLBACK', false),

    /** Ordered list of fallback levels with concurrency caps */
    FALLBACK_LEVELS: parseFallbackLevels(),

    /** Maximum total time (ms) across all fallback levels for one EAN */
    FALLBACK_TOTAL_TIMEOUT_MS: int('FALLBACK_TOTAL_TIMEOUT_MS', 180_000),

    /** Delay between fallback level switches (ms) - avoid slamming */
    FALLBACK_SWITCH_DELAY_MS: int('FALLBACK_SWITCH_DELAY_MS', 1000),

    /** Cost rates for metering */
    COST_RATES: Object.freeze<CostRates>({
        proxy_residential_per_gb_usd: float('COST_PROXY_RESIDENTIAL_PER_GB', 2.50),
        proxy_mobile_per_gb_usd: float('COST_PROXY_MOBILE_PER_GB', 12.00),
        proxy_sticky_per_gb_usd: float('COST_PROXY_STICKY_PER_GB', 3.50),
        proxy_datacenter_per_gb_usd: float('COST_PROXY_DATACENTER_PER_GB', 0.50),
        captcha_datadome_per_solve_usd: float('COST_CAPTCHA_DATADOME', 0.003),
        captcha_recaptcha_per_solve_usd: float('COST_CAPTCHA_RECAPTCHA', 0.002),
        browser_runtime_per_hour_usd: float('COST_BROWSER_RUNTIME_PER_HOUR', 0.15),
        antidetect_kameleo_per_min_usd: float('COST_ANTIDETECT_KAMELEO_PER_MIN', 0.008),
        antidetect_camoufox_per_min_usd: float('COST_ANTIDETECT_CAMOUFOX_PER_MIN', 0.0),
        antidetect_octo_per_min_usd: float('COST_ANTIDETECT_OCTO_PER_MIN', 0.006),
        antidetect_gologin_per_min_usd: float('COST_ANTIDETECT_GOLOGIN_PER_MIN', 0.005),
        estimated_kb_per_request_raw: float('COST_EST_KB_RAW', 50),
        estimated_kb_per_request_browser: float('COST_EST_KB_BROWSER', 800),
    }),

    /** Sticky proxy settings */
    STICKY_PROXY: Object.freeze<StickyProxyConfig>({
        residential_endpoint: opt('STICKY_PROXY_RESIDENTIAL_ENDPOINT', ''),
        residential_session_ttl_min: int('STICKY_PROXY_RESIDENTIAL_TTL_MIN', 30),
        mobile_endpoint: opt('STICKY_PROXY_MOBILE_ENDPOINT', ''),
        mobile_session_ttl_min: int('STICKY_PROXY_MOBILE_TTL_MIN', 60),
        proxy_username: opt('STICKY_PROXY_USERNAME', ''),
        proxy_password: opt('STICKY_PROXY_PASSWORD', ''),
    }),

    /** Antidetect browser config */
    ANTIDETECT: Object.freeze<AntidetectConfig>({
        default_tool: (opt('DEFAULT_ANTIDETECT_TOOL', 'camoufox') || null) as AntidetectTool,
        kameleo_api_url: opt('KAMELEO_API_URL', 'http://localhost:5050'),
        kameleo_api_password: opt('KAMELEO_API_PASSWORD', ''),
        camoufox_binary_path: opt('CAMOUFOX_BINARY_PATH', ''),
        profile_ttl_min: int('ANTIDETECT_PROFILE_TTL_MIN', 30),
    }),

    /** Stealth Playwright config */
    STEALTH_PLAYWRIGHT: Object.freeze<StealthPlaywrightConfig>({
        headless: bool('STEALTH_HEADLESS', true),
        viewport_width: int('STEALTH_VIEWPORT_WIDTH', 1920),
        viewport_height: int('STEALTH_VIEWPORT_HEIGHT', 1080),
        human_delay_min_ms: int('STEALTH_HUMAN_DELAY_MIN', 800),
        human_delay_max_ms: int('STEALTH_HUMAN_DELAY_MAX', 3000),
        scroll_steps: int('STEALTH_SCROLL_STEPS', 4),
        canvas_noise: bool('STEALTH_CANVAS_NOISE', true),
        ghost_cursor: bool('STEALTH_GHOST_CURSOR', true),
    }),
});
