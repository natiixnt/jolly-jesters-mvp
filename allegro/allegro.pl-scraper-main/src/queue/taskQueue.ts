import { nanoid } from 'nanoid';
import type { Task } from '@/types';
import { config } from '@/config';

export class TaskQueue {
    private store = new Map<string, Task>();
    private queue: string[] = [];
    private waiters: (() => void)[] = [];

    createTask(ean: string): Task {
        const task: Task = {
            id: nanoid(),
            ean,
            status: 'pending',
            retries: 0,
            result: null,
            error: null,
            createdAt: Date.now(),
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
        const id = this.queue.shift();
        if (!id) throw new Error('Queue unexpectedly empty');
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

    /** Requeue without incrementing retry counter (for session/proxy errors) */
    requeueNoRetry(id: string): void {
        const task = this.store.get(id);
        if (!task) return;
        task.status = 'pending';
        this.queue.unshift(id);
        this.notifyOne();
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

    private notifyOne(): void {
        const resolve = this.waiters.shift();
        if (resolve) resolve();
    }
}
