import { Mutex } from 'async-mutex';
import Allegro from '@/scraper/allegro';
import { getRandomProxy, nextProxy, proxyCount, proxyUrlHash } from '@/utils/proxy';
import { config } from '@/config';
import type { TaskQueue } from '@/queue/taskQueue';
import type { Stats } from '@/utils/stats';
import type { Logger, ScopedLogger } from '@/utils/logger';
import {
    isSevereBlock,
    executeWithFallback,
    isFallbackAvailable,
    calculateTaskCost,
    robustConfig,
    type RobustFetchResult,
    type RobustMetadata,
    defaultMetadata,
} from '@/robust';

const RETRYABLE_PATTERNS = [
    'IP is banned',
    'DataDome failed after',
    'Unexpected status',
    'Connection failed',
    'UNKNOWN_PROVIDER_ERROR',
    'CAPTCHA_SOLVE_FAILED',
];

function isRetryableError(msg: string): boolean {
    return RETRYABLE_PATTERNS.some((p) => msg.includes(p));
}

function shouldThrottle(scrapeCount: number): boolean {
    return scrapeCount === 0 || (scrapeCount >= 4 && scrapeCount <= 6);
}

/**
 * Attach default "raw" strategy metadata + cost to a result.
 * Called after every successful raw fetch so the result format matches
 * what the Python backend expects (same fields as fallback results).
 */
function attachRawMetadata(result: any, captchaSolves: number): void {
    const meta: RobustMetadata = defaultMetadata('raw', 0);
    meta.proxy_type = 'datacenter'; // raw strategy uses the loaded proxies (typically DC)

    // Calculate cost for the raw strategy
    const cost = calculateTaskCost({
        strategy: 'raw',
        fallback_level: 0,
        proxy_type: 'datacenter',
        antidetect_tool: null,
        captcha_solves: captchaSolves,
        datadome_solves: 0, // raw doesn't distinguish, counted as generic
        browser_runtime_ms: 0,
        estimated_kb: null,
    });

    // Merge metadata into result
    Object.assign(result, meta, {
        cost_breakdown: cost.cost_breakdown,
        total_cost_usd: cost.total_cost_usd,
    });
}

export class Worker {
    private allegro: Allegro;
    private readonly resetMutex = new Mutex();
    private readonly logger: ScopedLogger;
    private scrapeCount = 0;
    private maxActive = 1;
    private currentActive = 0;
    private readonly gateQueue: (() => void)[] = [];

    constructor(
        private readonly id: string,
        private readonly taskQueue: TaskQueue,
        private readonly stats: Stats,
        parentLogger: Logger,
    ) {
        this.logger = parentLogger.scoped(id);
        this.stats.registerWorker(id);
        this.allegro = new Allegro(getRandomProxy(), this.logger.scoped('Allegro'));
    }

    start(): void {
        for (let i = 0; i < config.CONCURRENCY_PER_WORKER; i++) {
            void this.loop();
        }
    }

    private async gateAcquire(): Promise<void> {
        while (this.currentActive >= this.maxActive) {
            await new Promise<void>((resolve) => this.gateQueue.push(resolve));
        }
        this.currentActive++;
    }

    private gateRelease(): void {
        this.currentActive--;
        this.drainGate();
    }

    private drainGate(): void {
        const available = this.maxActive - this.currentActive;
        for (let i = 0; i < available && this.gateQueue.length > 0; i++) {
            this.gateQueue.shift()!();
        }
    }

    private updateGate(): void {
        const desired = shouldThrottle(this.scrapeCount) ? 1 : config.CONCURRENCY_PER_WORKER;
        if (desired === this.maxActive) return;
        this.maxActive = desired;
        if (desired > 1) this.drainGate();
    }

    private async loop(): Promise<void> {
        while (true) {
            await this.gateAcquire();

            this.stats.setWorkerStatus(this.id, 'idle');
            const taskId = await this.taskQueue.dequeue();
            this.taskQueue.markProcessing(taskId);
            const task = this.taskQueue.getTask(taskId);

            if (!task) {
                this.gateRelease();
                continue;
            }

            this.stats.setWorkerStatus(this.id, 'active');
            this.stats.adjustActiveTasks(this.id, 1);

            try {
                await this.resetMutex.waitForUnlock();
                this.logger.log(`Scraping EAN ${task.ean} (attempt ${task.retries + 1})`);

                const proxyTries = Math.min(Math.max(proxyCount(), 1), 50);
                let result = null;
                const tried = new Set<string>();
                let rawCaptchaFailed = false;

                for (let attempt = 0; attempt < proxyTries; attempt++) {
                    const proxy =
                        attempt === 0 ? getRandomProxy() : nextProxy(tried);
                    tried.add(proxy.toString());
                    this.allegro = new Allegro(proxy, this.logger.scoped('Allegro'));
                    if (attempt > 0) {
                        this.logger.activity(
                            `EAN ${task.ean} retry ${attempt + 1}/${proxyTries} with new proxy`,
                            'info',
                        );
                    }
                    try {
                        result = await this.allegro.fetch(task.ean);
                        result.proxyAttempts = attempt + 1;
                        break;
                    } catch (err) {
                        const msg = err instanceof Error ? err.message : String(err);
                        this.logger.log(`Proxy attempt ${attempt + 1}/${proxyTries} failed: ${msg}`);

                        // If CAPTCHA solver fails, don't waste time on more proxy retries
                        // Escalate to Playwright fallback immediately
                        if (msg.includes('CAPTCHA_UNSOLVABLE') || msg.includes('INVALID_TASK_DATA') || msg.includes('CAPTCHA_SOLVE_FAILED')) {
                            this.logger.log(`CAPTCHA solver failed, escalating to fallback`);
                            rawCaptchaFailed = true;
                            break;
                        }

                        if (attempt === proxyTries - 1) throw err;
                        if (!isRetryableError(msg)) throw err;
                    }
                }

                // If CAPTCHA failed on raw, try Playwright fallback inline
                if (rawCaptchaFailed && isFallbackAvailable()) {
                    this.logger.log(`Trying Playwright fallback for EAN ${task.ean}`);
                    const fallbackResult = await executeWithFallback({
                        ean: task.ean,
                        taskId: task.id,
                        runId: task.runId,
                        rawError: 'CAPTCHA solver failed on raw strategy',
                    });
                    this.taskQueue.markCompleted(taskId, fallbackResult as any);
                    this.stats.recordTaskComplete(this.id, fallbackResult.durationMs);
                    this.stats.recordFallbackSuccess(fallbackResult.strategy, fallbackResult.fallback_level);
                    this.logger.activity(
                        `EAN ${task.ean} rescued by ${fallbackResult.strategy} (L${fallbackResult.fallback_level}) in ${fallbackResult.durationMs}ms`,
                        'success',
                    );
                    this.stats.adjustActiveTasks(this.id, -1);
                    this.gateRelease();
                    this.updateGate();
                    continue;
                }

                if (rawCaptchaFailed) {
                    throw new Error('CAPTCHA solver failed and no fallback available');
                }

                result.proxyUrlHash = proxyUrlHash(this.allegro.proxyUrl);
                result.proxySuccess = true;

                // Attach raw strategy metadata and cost to result
                attachRawMetadata(result, result.captchaSolves ?? 0);

                this.taskQueue.markCompleted(taskId, result);
                this.scrapeCount++;
                this.stats.recordTaskComplete(this.id, result.durationMs);
                for (let i = 0; i < result.captchaSolves; i++) this.stats.recordCaptchaSolve();
                this.logger.activity(`EAN ${task.ean} in ${String(result.durationMs)}ms [raw]`, 'success');
            } catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                this.logger.log(`Raw error EAN ${task.ean}: ${msg}`);

                // -------------------------------------------------------
                // ROBUST FALLBACK: if raw failed with a severe block or
                // CAPTCHA failure, try browser strategies immediately
                // (don't waste time on retries that will also fail)
                // -------------------------------------------------------
                const isCaptchaFailure = msg.includes('CAPTCHA_UNSOLVABLE') || msg.includes('INVALID_TASK_DATA') || msg.includes('CAPTCHA_SOLVE_FAILED');
                if (isFallbackAvailable() && (isSevereBlock(msg) || isCaptchaFailure)) {
                    this.logger.log(`Escalating EAN ${task.ean} to fallback chain`);
                    try {
                        const fallbackResult = await executeWithFallback({
                            ean: task.ean,
                            taskId: task.id,
                            runId: task.runId,
                            rawError: msg,
                        });

                        this.taskQueue.markCompleted(taskId, fallbackResult as any);
                        this.stats.recordTaskComplete(this.id, fallbackResult.durationMs);
                        this.stats.recordFallbackSuccess(fallbackResult.strategy, fallbackResult.fallback_level);
                        for (let i = 0; i < fallbackResult.captchaSolves; i++) this.stats.recordCaptchaSolve();
                        this.logger.activity(
                            `EAN ${task.ean} rescued by ${fallbackResult.strategy} (L${fallbackResult.fallback_level}) in ${fallbackResult.durationMs}ms`,
                            'success',
                        );

                        // Don't fall through to the error handling below
                        this.stats.adjustActiveTasks(this.id, -1);
                        this.gateRelease();
                        this.updateGate();
                        continue;
                    } catch (fallbackErr) {
                        const fbMsg = fallbackErr instanceof Error ? fallbackErr.message : String(fallbackErr);
                        this.logger.log(`Fallback chain also failed for EAN ${task.ean}: ${fbMsg}`);
                        this.stats.recordFallbackFailure();
                        // Fall through to normal error handling
                    }
                }

                // If Allegro returned HTML (unexpected token "<"), treat as no_results instead of hard fail.
                if (msg.includes("Unexpected token '<'")) {
                    const noResult: any = {
                        status: 'no_results',
                        ean: task.ean,
                        totalOfferCount: 0,
                        products: [],
                        durationMs: 0,
                        scrapedAt: new Date().toISOString(),
                        captchaSolves: 0,
                        html: '',
                    };
                    attachRawMetadata(noResult, 0);
                    this.taskQueue.markCompleted(taskId, noResult);
                    this.logger.activity(`EAN ${task.ean} marked no_results (html response)`, 'info');
                    this.stats.recordTaskComplete(this.id, 0);
                    this.gateRelease();
                    this.updateGate();
                    continue;
                }

                if (isRetryableError(msg)) {
                    if (!this.taskQueue.requeueNoRetry(taskId)) {
                        this.taskQueue.markFailed(taskId, `max_soft_retries: ${msg}`);
                        this.stats.recordTaskFailed();
                        this.logger.activity(`EAN ${task.ean} failed (max soft retries)`, 'error');
                    } else {
                        this.logger.activity(`EAN ${task.ean} requeued (${msg})`, 'info');
                    }
                    await this.resetSession();
                } else if (this.taskQueue.requeue(taskId)) {
                    this.logger.activity(`EAN ${task.ean} requeued (${msg})`, 'info');
                } else {
                    this.taskQueue.markFailed(taskId, msg);
                    this.stats.recordTaskFailed();
                    this.logger.activity(`EAN ${task.ean}: ${msg}`, 'error');
                }
            } finally {
                this.stats.adjustActiveTasks(this.id, -1);
                this.gateRelease();
                this.updateGate();
            }
        }
    }

    private async resetSession(): Promise<void> {
        if (this.resetMutex.isLocked()) {
            await this.resetMutex.waitForUnlock();
            return;
        }
        await this.resetMutex.runExclusive(async () => {
            this.stats.setWorkerStatus(this.id, 'resetting');
            this.logger.log('Resetting session...');
            await this.allegro.destroySession();
            this.allegro = new Allegro(getRandomProxy(), this.logger.scoped('Allegro'));
            this.scrapeCount = 0;
            this.maxActive = 1;
            this.stats.recordSessionReset(this.id);
            this.logger.activity('Session reset with new proxy', 'info');
        });
    }
}
