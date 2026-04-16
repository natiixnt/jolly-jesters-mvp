import type { AllegroFetchResult } from '@/scraper/allegro';
import type { RobustMetadata } from '@/robust/types';

export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed';

/**
 * Task result is the base AllegroFetchResult extended with optional
 * robust metadata. Raw strategy tasks will have the metadata fields
 * populated with defaults; fallback tasks will have full metadata.
 */
export type TaskResult = AllegroFetchResult & Partial<RobustMetadata>;

export interface Task {
    id: string;
    ean: string;
    status: TaskStatus;
    retries: number;
    softRetries: number;
    result: TaskResult | null;
    error: string | null;
    createdAt: number;
    runId?: string;
}

export interface CreateTaskBody {
    ean: string;
    runId?: string;
}

export interface TaskResponse {
    taskId: string;
    status: TaskStatus;
    result: (Omit<TaskResult, 'html'>) | null;
    error: string | null;
    retries?: number;
}
