import { Worker } from '@/worker/worker';
import { config } from '@/config';
import type { TaskQueue } from '@/queue/taskQueue';
import type { Stats } from '@/utils/stats';
import type { Logger } from '@/utils/logger';

export class WorkerPool {
    private workers: Worker[] = [];

    constructor(taskQueue: TaskQueue, stats: Stats, logger: Logger) {
        for (let i = 0; i < config.WORKER_COUNT; i++) {
            this.workers.push(new Worker(`W-${String(i + 1)}`, taskQueue, stats, logger));
        }
    }

    start(): void {
        for (const worker of this.workers) {
            worker.start();
        }
    }
}
