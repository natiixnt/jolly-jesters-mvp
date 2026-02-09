import type { AllegroFetchResult } from '@/scraper/allegro';

export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed';

export interface Task {
    id: string;
    ean: string;
    status: TaskStatus;
    retries: number;
    result: AllegroFetchResult | null;
    error: string | null;
    createdAt: number;
}

export interface CreateTaskBody {
    ean: string;
}

export interface TaskResponse {
    taskId: string;
    status: TaskStatus;
    result: Omit<AllegroFetchResult, 'html'> | null;
    error: string | null;
}
