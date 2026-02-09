import { Pool } from 'undici';
import type { ScopedLogger } from '@/utils/logger';

type errorId = 0 | 1 | 2;

interface AnySolverError {
    errorId: errorId;
    errorCode: string;
    errorDescription: string;
}

interface CreateTaskResponse {
    errorId: errorId;
    taskId: string;
}

interface GetTaskResultResponse {
    errorId: errorId;
    status: 'processing' | 'ready' | 'failed';
    solution?: Record<string, unknown>;
    errorCode?: string;
    errorDescription?: string;
}

export class AnySolver {
    private client: Pool;
    private clientKey: string;
    private logger: ScopedLogger;

    constructor(clientKey: string, logger: ScopedLogger) {
        this.clientKey = clientKey;
        this.logger = logger;
        this.client = new Pool('https://api.anysolver.io');
    }

    private async post<T>(path: string, body: Record<string, unknown>): Promise<T> {
        this.logger.log('POST', path);

        const res = await this.client.request({
            method: 'POST',
            path,
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ clientKey: this.clientKey, ...body, provider: 'CapSolver' }),
        });
        const data = (await res.body.json()) as T & AnySolverError;
        if (data.errorId !== 0) {
            this.logger.log('Error', data.errorCode, data.errorDescription);
            throw new Error(`${data.errorCode}: ${data.errorDescription}`);
        }
        this.logger.log('Response', JSON.stringify(data));
        return data;
    }

    async createTask(task: Record<string, unknown>): Promise<string> {
        this.logger.log('Creating task', JSON.stringify(task));
        const res = await this.post<CreateTaskResponse>('/createTask', { task });
        this.logger.log('Task created', res.taskId);
        return res.taskId;
    }

    async getTaskResult(taskId: string): Promise<GetTaskResultResponse> {
        return this.post<GetTaskResultResponse>('/getTaskResult', { taskId });
    }

    async solve(task: Record<string, unknown>, interval = 3000, maxRetries = 3): Promise<Record<string, unknown>> {
        for (let attempt = 1; attempt <= maxRetries; attempt++) {
            try {
                const taskId = await this.createTask(task);
                this.logger.log('Polling every', interval, 'ms (attempt', attempt, 'of', maxRetries + ')');
                while (true) {
                    await new Promise((r) => setTimeout(r, interval));
                    const result = await this.getTaskResult(taskId);
                    if (result.status === 'ready') {
                        this.logger.log('Solved');
                        return result.solution ?? {};
                    }
                    if (result.status === 'failed') {
                        throw new Error(`Task failed: ${result.errorCode ?? 'unknown'}`);
                    }
                }
            } catch (error) {
                this.logger.log('Attempt', attempt, 'failed:', error);
                if (attempt === maxRetries) {
                    throw error;
                }
                this.logger.log('Retrying...');
            }
        }
        throw new Error('Max retries exceeded');
    }
}
