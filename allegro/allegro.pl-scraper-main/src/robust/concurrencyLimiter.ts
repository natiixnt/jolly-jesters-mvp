/**
 * Per-strategy concurrency limiter (semaphore).
 *
 * Browser-based strategies are much more resource-intensive than raw HTTP.
 * This limiter ensures we don't launch too many concurrent browser instances:
 *   - stealthPlaywright: max 4 (configurable)
 *   - antidetectBrowser: max 2
 *   - mobileFallback: max 2
 *
 * The limiter uses a simple semaphore pattern with a FIFO wait queue.
 * Workers acquire a slot before running a browser strategy and release
 * it when done (or on error via finally blocks).
 */

import { robustConfig } from './config';
import type { StrategyName } from './types';

// ---------------------------------------------------------------------------
// Semaphore implementation
// ---------------------------------------------------------------------------

class Semaphore {
    private current = 0;
    private readonly waiters: (() => void)[] = [];

    constructor(private readonly max: number) {}

    async acquire(): Promise<void> {
        while (this.current >= this.max) {
            await new Promise<void>((resolve) => this.waiters.push(resolve));
        }
        this.current++;
    }

    release(): void {
        this.current--;
        const next = this.waiters.shift();
        if (next) next();
    }

    get activeCount(): number {
        return this.current;
    }

    get waitingCount(): number {
        return this.waiters.length;
    }

    get maxSlots(): number {
        return this.max;
    }
}

// ---------------------------------------------------------------------------
// Per-strategy semaphores (created lazily from config)
// ---------------------------------------------------------------------------

const semaphores = new Map<StrategyName, Semaphore>();

function getSemaphore(strategy: StrategyName): Semaphore {
    let sem = semaphores.get(strategy);
    if (!sem) {
        const levelCfg = robustConfig.FALLBACK_LEVELS.find((l) => l.name === strategy);
        const max = levelCfg?.maxConcurrency ?? 2;
        sem = new Semaphore(max);
        semaphores.set(strategy, sem);
    }
    return sem;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Acquire a concurrency slot for the given strategy.
 * Blocks (awaits) if all slots are occupied.
 */
export async function acquireSlot(strategy: StrategyName): Promise<void> {
    // Raw strategy has no concurrency limit at this layer
    if (strategy === 'raw') return;
    await getSemaphore(strategy).acquire();
}

/**
 * Release a concurrency slot for the given strategy.
 * MUST be called in a finally block after acquireSlot.
 */
export function releaseSlot(strategy: StrategyName): void {
    if (strategy === 'raw') return;
    getSemaphore(strategy).release();
}

/**
 * Get current concurrency stats for all strategies (for /health endpoint).
 */
export function getConcurrencyStats(): Record<string, { active: number; waiting: number; max: number }> {
    const result: Record<string, { active: number; waiting: number; max: number }> = {};
    for (const level of robustConfig.FALLBACK_LEVELS) {
        if (level.name === 'raw') continue;
        const sem = semaphores.get(level.name);
        result[level.name] = {
            active: sem?.activeCount ?? 0,
            waiting: sem?.waitingCount ?? 0,
            max: level.maxConcurrency,
        };
    }
    return result;
}

/**
 * Run a function with a concurrency slot, releasing it on completion or error.
 */
export async function withSlot<T>(strategy: StrategyName, fn: () => Promise<T>): Promise<T> {
    await acquireSlot(strategy);
    try {
        return await fn();
    } finally {
        releaseSlot(strategy);
    }
}
