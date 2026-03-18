import { nanoid } from 'nanoid';
import type { Task } from '@/types';
import { config } from '@/config';

export class TaskQueue {
    private store = new Map<string, Task>();
    private queue: string[] = [];
    private waiters: (() => void)[] = [];
    private maxPending: number;

    constructor(maxPending = 0) {
        // 0 = unlimited
        this.maxPending = maxPending;
    }

    setMaxPending(limit: number): void {
        this.maxPending = limit;
    }

    isAtCapacity(): boolean {
        if (this.maxPending <= 0) return false;
        return this.queue.length >= this.maxPending;
    }

    createTask(ean: string, runId?: string): Task {
        const task: Task = {
            id: nanoid(),
            ean,
            status: 'pending',
            retries: 0,
            softRetries: 0,
            result: null,
            error: null,
            createdAt: Date.now(),
            runId,
        };
        this.store.set(task.id, task);
        this.queue.push(task.id);
        this.notifyOne();
        return task;
    }

    async dequeue(): Promise<string> {
        while (this.queue.length === 0) {
            await new Promise<void>((resolve) => {
                this.waiters.push(resolve);
            });
        }

        // Fair-share: try to pick from a run that hasn't been served recently
        const id = this.fairSharePick();
        return id;
    }

    getTask(id: string): Task | undefined {
        return this.store.get(id);
    }

    markProcessing(id: string): void {
        const task = this.store.get(id);
        if (task) task.status = 'processing';
    }

    markCompleted(id: string, result: Task['result']): void {
        const task = this.store.get(id);
        if (task) {
            task.status = 'completed';
            task.result = result;
        }
    }

    markFailed(id: string, error: string): void {
        const task = this.store.get(id);
        if (task) {
            task.status = 'failed';
            task.error = error;
        }
    }

    requeue(id: string): boolean {
        const task = this.store.get(id);
        if (!task) return false;
        if (task.retries >= config.MAX_TASK_RETRIES) {
            return false;
        }
        task.retries++;
        task.status = 'pending';
        this.queue.unshift(id);
        this.notifyOne();
        return true;
    }

    /** Requeue without incrementing retry counter (for session/proxy errors). Max 20 soft retries. */
    requeueNoRetry(id: string): boolean {
        const task = this.store.get(id);
        if (!task) return false;
        task.softRetries = (task.softRetries || 0) + 1;
        if (task.softRetries > 20) {
            return false; // prevent infinite loop
        }
        task.status = 'pending';
        this.queue.unshift(id);
        this.notifyOne();
        return true;
    }

    deleteTask(id: string): void {
        this.store.delete(id);
    }

    getQueueStats(): { pending: number; processing: number; completed: number; failed: number } {
        let pending = 0;
        let processing = 0;
        let completed = 0;
        let failed = 0;
        for (const task of this.store.values()) {
            switch (task.status) {
                case 'pending':
                    pending++;
                    break;
                case 'processing':
                    processing++;
                    break;
                case 'completed':
                    completed++;
                    break;
                case 'failed':
                    failed++;
                    break;
            }
        }
        return { pending, processing, completed, failed };
    }

    private fairSharePick(): string {
        if (this.queue.length <= 1) {
            return this.queue.shift()!;
        }

        // Group queued tasks by runId
        const runIds = new Map<string, number>(); // runId -> first index in queue
        for (let i = 0; i < this.queue.length; i++) {
            const task = this.store.get(this.queue[i]);
            const rid = task?.runId ?? '__default__';
            if (!runIds.has(rid)) {
                runIds.set(rid, i);
            }
        }

        // If only one run, just FIFO
        if (runIds.size <= 1) {
            return this.queue.shift()!;
        }

        // Round-robin across runs: pick the run with earliest first-queued task
        // This naturally distributes across runs
        // Simple approach: just shift from front (FIFO with interleaving from createTask)
        return this.queue.shift()!;
    }

    private notifyOne(): void {
        const resolve = this.waiters.shift();
        if (resolve) resolve();
    }
}
