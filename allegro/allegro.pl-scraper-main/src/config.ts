import { getEnv } from '@/utils/env';

const int = (key: string, fallback: number): number => {
    const v = process.env[key];
    return v ? parseInt(v, 10) : fallback;
};

const bool = (key: string, fallback: boolean): boolean => {
    const v = process.env[key];
    if (!v) return fallback;
    return v === 'true' || v === '1';
};

export const config = Object.freeze({
    PORT: int('PORT', 3000),
    WORKER_COUNT: int('WORKER_COUNT', 1),
    CONCURRENCY_PER_WORKER: int('CONCURRENCY_PER_WORKER', 1),
    MAX_TASK_RETRIES: int('MAX_TASK_RETRIES', 3),
    DEBUG: bool('DEBUG', false),
    ANYSOLVER_API_KEY: getEnv('ANYSOLVER_API_KEY'),

    // Executor config
    EANS_FILE: process.env['EANS_FILE'] ?? 'eans.txt',
    OUTPUT_DIR: process.env['OUTPUT_DIR'] ?? 'output',
    API_BASE_URL: process.env['API_BASE_URL'] ?? 'http://localhost:3000',
    POLL_INTERVAL: int('POLL_INTERVAL', 1000),
});
