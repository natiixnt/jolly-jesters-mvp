import { Mutex } from 'async-mutex';
import Allegro from '@/scraper/allegro';
import { getRandomProxy, nextProxy, proxyCount } from '@/utils/proxy';
import { config } from '@/config';
import type { TaskQueue } from '@/queue/taskQueue';
import type { Stats } from '@/utils/stats';
import type { Logger, ScopedLogger } from '@/utils/logger';

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

                for (let attempt = 0; attempt < proxyTries; attempt++) {
                    // pick proxy (first: current instance, else next)
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
                        break;
                    } catch (err) {
                        const msg = err instanceof Error ? err.message : String(err);
                        this.logger.log(`Proxy attempt ${attempt + 1}/${proxyTries} failed: ${msg}`);
                        if (attempt === proxyTries - 1) throw err;
                        if (!isRetryableError(msg)) throw err;
                    }
                }

                this.taskQueue.markCompleted(taskId, result);
                this.scrapeCount++;
                this.stats.recordTaskComplete(this.id, result.durationMs);
                for (let i = 0; i < result.captchaSolves; i++) this.stats.recordCaptchaSolve();
                this.logger.activity(`EAN ${task.ean} in ${String(result.durationMs)}ms`, 'success');
            } catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                this.logger.log(`Error EAN ${task.ean}: ${msg}`);

                // If Allegro returned HTML (unexpected token "<"), treat as no_results instead of hard fail.
                if (msg.includes("Unexpected token '<'")) {
                    this.taskQueue.markCompleted(taskId, {
                        status: 'no_results',
                        ean: task.ean,
                        totalOfferCount: 0,
                        products: [],
                        durationMs: 0,
                        scrapedAt: new Date().toISOString(),
                        captchaSolves: 0,
                        html: '',
                    } as any);
                    this.logger.activity(`EAN ${task.ean} marked no_results (html response)`, 'info');
                    this.stats.recordTaskComplete(this.id, 0);
                    this.gateRelease();
                    this.updateGate();
                    continue;
                }

                if (isRetryableError(msg)) {
                    this.taskQueue.requeueNoRetry(taskId);
                    this.logger.activity(`EAN ${task.ean} requeued (${msg})`, 'info');
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
