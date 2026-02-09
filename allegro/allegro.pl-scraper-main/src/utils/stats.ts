import type { TaskQueue } from '@/queue/taskQueue';

export interface LogEntry {
    time: number;
    workerId: string;
    message: string;
    type: 'success' | 'error' | 'info';
}

export interface WorkerState {
    id: string;
    status: 'idle' | 'active' | 'solving' | 'resetting';
    activeTasks: number;
    sessionStartedAt: number | null;
    sessionResets: number;
    sessionScrapes: number;
}

class RunningStats {
    min = Infinity;
    max = 0;
    avg = 0;
    count = 0;
    private alpha = 0.1;

    record(value: number): void {
        this.count++;
        if (value < this.min) this.min = value;
        if (value > this.max) this.max = value;
        this.avg = this.count === 1 ? value : this.avg * (1 - this.alpha) + value * this.alpha;
    }
}

export const LOG_RING_SIZE = 8;
const THROUGHPUT_WINDOW_MS = 60_000;

export class Stats {
    readonly taskDuration = new RunningStats();
    readonly sessionDuration = new RunningStats();
    readonly sessionScrapes = new RunningStats();
    totalCompleted = 0;
    totalFailed = 0;
    totalCaptchaSolves = 0;
    totalSessionResets = 0;
    readonly workers = new Map<string, WorkerState>();
    readonly logs: LogEntry[] = [];
    readonly startedAt = Date.now();
    taskQueue: TaskQueue | null = null;

    private completionTimes: number[] = [];

    registerWorker(id: string): void {
        this.workers.set(id, {
            id,
            status: 'idle',
            activeTasks: 0,
            sessionStartedAt: null,
            sessionResets: 0,
            sessionScrapes: 0,
        });
    }

    setWorkerStatus(id: string, status: WorkerState['status']): void {
        const w = this.workers.get(id);
        if (w) w.status = status;
    }

    adjustActiveTasks(id: string, delta: number): void {
        const w = this.workers.get(id);
        if (w) w.activeTasks = Math.max(0, w.activeTasks + delta);
    }

    recordTaskComplete(workerId: string, durationMs: number): void {
        this.totalCompleted++;
        this.taskDuration.record(durationMs);

        const w = this.workers.get(workerId);
        if (w) {
            if (w.sessionStartedAt === null) w.sessionStartedAt = Date.now();
            w.sessionScrapes++;
        }

        this.completionTimes.push(Date.now());
    }

    recordTaskFailed(): void {
        this.totalFailed++;
    }

    recordCaptchaSolve(): void {
        this.totalCaptchaSolves++;
    }

    recordSessionReset(workerId: string): void {
        this.totalSessionResets++;
        const w = this.workers.get(workerId);
        if (w) {
            if (w.sessionStartedAt !== null && w.sessionScrapes > 0) {
                this.sessionDuration.record(Date.now() - w.sessionStartedAt);
                this.sessionScrapes.record(w.sessionScrapes);
            }
            w.sessionResets++;
            w.sessionStartedAt = null;
            w.sessionScrapes = 0;
        }
    }

    pushLog(entry: LogEntry): void {
        this.logs.push(entry);
        if (this.logs.length > LOG_RING_SIZE) this.logs.shift();
    }

    getTasksPerHour(): number {
        const now = Date.now();
        const cutoff = now - THROUGHPUT_WINDOW_MS;
        this.completionTimes = this.completionTimes.filter((t) => t >= cutoff);
        if (this.completionTimes.length === 0) return 0;
        const windowMs = now - this.completionTimes[0]!;
        if (windowMs < 1000) return 0;
        return Math.round((this.completionTimes.length / windowMs) * 3_600_000);
    }
}
