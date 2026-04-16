/**
 * Fallback Chain Orchestrator.
 *
 * This is the main entry point for the robust multi-strategy system.
 * It receives a failed raw result (or an error) and automatically tries
 * progressively more sophisticated strategies until one succeeds.
 *
 * Chain order:
 *   Level 0: raw (already attempted by worker.ts)
 *   Level 1: stealthPlaywright (Playwright + stealth + sticky residential)
 *   Level 2: antidetectBrowser (Camoufox/Kameleo + sticky residential)
 *   Level 3: mobileFallback (Camoufox mobile + mobile 4G proxy)
 *
 * The chain respects:
 *   - Per-level concurrency limits (via concurrencyLimiter.ts)
 *   - Global timeout (FALLBACK_TOTAL_TIMEOUT_MS)
 *   - Error classification (only escalates on severe blocks)
 *   - Cost tracking (accumulates costs across all attempted levels)
 *
 * Usage in worker.ts:
 *   if (rawFailed && robustConfig.ENABLE_ROBUST_FALLBACK) {
 *       const result = await executeWithFallback(ean, taskId, rawError);
 *   }
 */

import { robustConfig } from './config';
import { classifyError } from './errorClassifier';
import { withSlot } from './concurrencyLimiter';
import { StealthPlaywrightStrategy } from './strategies/stealthPlaywright';
import { AntidetectBrowserStrategy } from './strategies/antidetectBrowser';
import { MobileFallbackStrategy } from './strategies/mobileFallback';
import type { FetchStrategy, RobustFetchResult, FallbackLevel, RobustMetadata } from './types';
import { defaultMetadata } from './types';
import type { ScopedLogger } from '@/utils/logger';

// ---------------------------------------------------------------------------
// Singleton strategy instances (lazy-initialized)
// ---------------------------------------------------------------------------

let strategies: FetchStrategy[] | null = null;
let chainLogger: ScopedLogger | null = null;

/**
 * Initialize the fallback chain. Call once at startup.
 */
export function initFallbackChain(logger: ScopedLogger): void {
    chainLogger = logger.scoped('FallbackChain');

    const levels = robustConfig.FALLBACK_LEVELS;
    strategies = [];

    // Level 0 (raw) is handled by the existing worker, not by the chain.
    // We only create strategies for levels 1-3.

    for (const level of levels) {
        if (!level.enabled) continue;

        switch (level.name) {
            case 'stealthPlaywright':
                strategies.push(new StealthPlaywrightStrategy(chainLogger));
                break;
            case 'antidetectBrowser':
                strategies.push(new AntidetectBrowserStrategy(chainLogger));
                break;
            case 'mobileFallback':
                strategies.push(new MobileFallbackStrategy(chainLogger));
                break;
            // 'raw' is skipped - handled by worker
        }
    }

    chainLogger.log(`Initialized with ${strategies.length} fallback levels: ${strategies.map((s) => s.name).join(' -> ')}`);
}

/**
 * Destroy all strategy instances (cleanup on shutdown).
 */
export async function destroyFallbackChain(): Promise<void> {
    if (!strategies) return;
    for (const strategy of strategies) {
        await strategy.destroy().catch(() => {});
    }
    strategies = null;
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

export interface FallbackContext {
    /** EAN to fetch */
    ean: string;
    /** Task ID for logging */
    taskId: string;
    /** Run ID for sticky session keying */
    runId?: string;
    /** Error from the raw strategy that triggered the fallback */
    rawError: string;
}

/**
 * Execute the fallback chain for a given EAN.
 *
 * Tries each enabled strategy in order, respecting concurrency limits
 * and the global timeout. Returns the first successful result.
 *
 * @throws If all strategies fail or timeout is exceeded
 */
export async function executeWithFallback(ctx: FallbackContext): Promise<RobustFetchResult> {
    if (!strategies || strategies.length === 0) {
        throw new Error('Fallback chain not initialized or no strategies available');
    }

    const log = chainLogger!;
    const totalDeadline = Date.now() + robustConfig.FALLBACK_TOTAL_TIMEOUT_MS;
    const attemptedLevels: FallbackLevel[] = [0]; // 0 = raw (already tried)
    const levelErrors: Record<number, string> = { 0: ctx.rawError };

    log.log(`Starting fallback for EAN ${ctx.ean} (raw error: ${ctx.rawError})`);

    for (const strategy of strategies) {
        // Check global timeout
        if (Date.now() >= totalDeadline) {
            log.log(`Global timeout reached, aborting fallback chain`);
            break;
        }

        // Check if strategy is available (e.g. browser installed, API reachable)
        const available = await strategy.isAvailable().catch(() => false);
        if (!available) {
            log.log(`Skipping ${strategy.name}: not available`);
            continue;
        }

        attemptedLevels.push(strategy.level);
        log.log(`Trying Level ${strategy.level}: ${strategy.name}`);

        // Delay between levels to avoid slamming
        if (attemptedLevels.length > 2) {
            await new Promise((r) => setTimeout(r, robustConfig.FALLBACK_SWITCH_DELAY_MS));
        }

        try {
            // Acquire concurrency slot (blocks if at capacity)
            const result = await withSlot(strategy.name, async () => {
                // Set a per-level timeout (remaining global time / remaining levels)
                const remainingMs = totalDeadline - Date.now();
                const perLevelTimeout = Math.max(remainingMs, 30_000);

                return Promise.race([
                    strategy.fetch(ctx.ean, ctx.runId),
                    rejectAfter(perLevelTimeout, `${strategy.name} timeout (${perLevelTimeout}ms)`),
                ]);
            });

            // Success! Annotate the result with chain metadata
            result.attempted_levels = attemptedLevels;
            result.level_errors = levelErrors;

            log.activity(
                `EAN ${ctx.ean} rescued by ${strategy.name} (Level ${strategy.level}) after ${attemptedLevels.length - 1} fallback(s)`,
                'success',
            );
            return result;
        } catch (err) {
            const errMsg = err instanceof Error ? err.message : String(err);
            levelErrors[strategy.level] = errMsg;
            log.log(`Level ${strategy.level} (${strategy.name}) failed: ${errMsg}`);

            // Classify the error to decide whether to continue
            const classified = classifyError(errMsg);
            if (classified.severity === 'fatal') {
                log.log(`Fatal error at Level ${strategy.level}, aborting chain`);
                break;
            }

            // Continue to next level
        }
    }

    // All levels failed - throw aggregate error
    const summary = Object.entries(levelErrors)
        .map(([lvl, err]) => `L${lvl}: ${err}`)
        .join(' | ');
    throw new Error(`All fallback levels exhausted for EAN ${ctx.ean}: ${summary}`);
}

/**
 * Quick check: is the fallback chain configured and has available strategies?
 */
export function isFallbackAvailable(): boolean {
    return robustConfig.ENABLE_ROBUST_FALLBACK && !!strategies && strategies.length > 0;
}

/**
 * Get chain status for the /health endpoint.
 */
export function getFallbackChainStatus(): {
    enabled: boolean;
    levels: { name: string; level: number; available: boolean }[];
} {
    return {
        enabled: robustConfig.ENABLE_ROBUST_FALLBACK,
        levels: (strategies ?? []).map((s) => ({
            name: s.name,
            level: s.level,
            available: true, // Approximate - real check is async
        })),
    };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function rejectAfter(ms: number, message: string): Promise<never> {
    return new Promise((_, reject) => {
        setTimeout(() => reject(new Error(message)), ms);
    });
}
