/**
 * Error classifier for the fallback chain.
 *
 * Determines whether an error from a strategy is:
 *   - SEVERE_BLOCK: IP/fingerprint permanently banned, must escalate to next level
 *   - SOFT_BLOCK: temporary rate limit or CAPTCHA wall, can retry same level
 *   - TRANSIENT: network glitch, can retry same level with different proxy
 *   - FATAL: non-recoverable (e.g. EAN invalid), stop entire chain
 *
 * The fallback chain uses this classification to decide whether to retry the
 * current level or escalate to the next one.
 */

export type BlockSeverity = 'severe_block' | 'soft_block' | 'transient' | 'fatal';

export interface ClassifiedError {
    severity: BlockSeverity;
    /** Original error message */
    message: string;
    /** Whether to escalate to the next fallback level */
    shouldEscalate: boolean;
    /** Whether the error is retryable at the same level */
    retryableAtSameLevel: boolean;
}

// ---------------------------------------------------------------------------
// Pattern matching
// ---------------------------------------------------------------------------

/** Patterns that indicate the IP or fingerprint is permanently banned */
const SEVERE_BLOCK_PATTERNS = [
    'IP is banned',
    'IP is banned by DataDome (t=bv)',
    'DataDome failed after',
    'blocked_rate exceeded',
    'Access denied',
    'Forbidden',
    // CAPTCHA solver failures - must escalate to browser-based fallback
    'CAPTCHA_SOLVE_FAILED',
    'CAPTCHA_UNSOLVABLE',
    'INVALID_TASK_DATA',
    'max_soft_retries',
];

/** Patterns that indicate a temporary block (worth retrying with same strategy) */
const SOFT_BLOCK_PATTERNS = [
    'Unexpected status: 429',
    'Unexpected status: 403',
    'allegrocaptcha.com',
    'captcha-delivery.com',
    'rate-limiter',
    'Unexpected status: 503',
];

/** Patterns that indicate a transient network issue */
const TRANSIENT_PATTERNS = [
    'Connection failed',
    'ECONNRESET',
    'ECONNREFUSED',
    'ETIMEDOUT',
    'socket hang up',
    'network timeout',
    'fetch failed',
    'UNKNOWN_PROVIDER_ERROR',
    'Unexpected status: 0',
];

/** Patterns that indicate a fatal, non-recoverable error */
const FATAL_PATTERNS = [
    'Invalid EAN',
    'Could not extract',
    'Unsupported captcha',
];

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Classify an error to determine the appropriate fallback action.
 */
export function classifyError(error: Error | string): ClassifiedError {
    const msg = typeof error === 'string' ? error : error.message;

    // Check fatal first (no retry, no escalation)
    if (matchesAny(msg, FATAL_PATTERNS)) {
        return { severity: 'fatal', message: msg, shouldEscalate: false, retryableAtSameLevel: false };
    }

    // Severe block -> must escalate to next fallback level
    if (matchesAny(msg, SEVERE_BLOCK_PATTERNS)) {
        return { severity: 'severe_block', message: msg, shouldEscalate: true, retryableAtSameLevel: false };
    }

    // Soft block -> retry at same level (maybe with new proxy), but escalate if retries exhausted
    if (matchesAny(msg, SOFT_BLOCK_PATTERNS)) {
        return { severity: 'soft_block', message: msg, shouldEscalate: false, retryableAtSameLevel: true };
    }

    // Transient -> retry at same level
    if (matchesAny(msg, TRANSIENT_PATTERNS)) {
        return { severity: 'transient', message: msg, shouldEscalate: false, retryableAtSameLevel: true };
    }

    // Unknown error -> treat as severe block (conservative: escalate)
    return { severity: 'severe_block', message: msg, shouldEscalate: true, retryableAtSameLevel: false };
}

/**
 * Quick check: is this error severe enough to warrant fallback escalation?
 * Used by the worker to decide whether to invoke the fallback chain.
 */
export function isSevereBlock(error: Error | string): boolean {
    const classified = classifyError(error);
    return classified.shouldEscalate;
}

/**
 * Check if a raw strategy result indicates a block that warrants fallback.
 * Inspects scrape_status and error patterns from the raw Allegro fetch result.
 */
export function isResultBlocked(result: { status?: string; error?: string }): boolean {
    const status = result.status ?? '';
    if (status === 'blocked' || status === 'network_error' || status === 'error') {
        return true;
    }
    if (result.error && isSevereBlock(result.error)) {
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function matchesAny(msg: string, patterns: string[]): boolean {
    return patterns.some((p) => msg.includes(p));
}
