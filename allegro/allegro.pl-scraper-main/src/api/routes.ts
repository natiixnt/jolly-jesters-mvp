import { Hono } from 'hono';
import { config } from '@/config';
import type { TaskQueue } from '@/queue/taskQueue';
import type { CreateTaskBody, TaskResponse } from '@/types';
import { proxiesMeta, reloadProxies } from '@/utils/proxy';
import type { Stats } from '@/utils/stats';
import { getConcurrencyStats, getActiveSessionCount, getFallbackChainStatus, robustConfig } from '@/robust';

export function createRoutes(taskQueue: TaskQueue, startWorkers: () => void, stats: Stats): Hono {
    const app = new Hono();

    app.post('/createTask', async (c) => {
        const body = await c.req.json<CreateTaskBody>();
        if (!body.ean || !/^\d{8,13}$/.test(body.ean)) {
            return c.json({ error: 'Invalid EAN (8-13 digits required)' }, 400);
        }
        if (taskQueue.isAtCapacity()) {
            return c.json({ error: 'Queue at capacity, retry later' }, 429);
        }
        const task = taskQueue.createTask(body.ean, body.runId);
        return c.json({ taskId: task.id }, 201);
    });

    app.get('/getTaskResult/:taskId', (c) => {
        const task = taskQueue.getTask(c.req.param('taskId'));
        if (!task) {
            return c.json({ error: 'Task not found' }, 404);
        }

        const response: TaskResponse = {
            taskId: task.id,
            status: task.status,
            result: null,
            error: task.error,
            retries: task.retries,
        };

        if (task.result) {
            const { html: _html, ...rest } = task.result;
            response.result = rest;
        }

        if (task.status === 'completed' || task.status === 'failed') {
            taskQueue.deleteTask(task.id);
        }

        return c.json(response);
    });

    app.get('/health', (c) =>
        c.json({
            status: 'ok',
            workerCount: config.WORKER_COUNT,
            concurrencyPerWorker: config.CONCURRENCY_PER_WORKER,
            maxTaskRetries: config.MAX_TASK_RETRIES,
            pollInterval: config.POLL_INTERVAL / 1000,
            timeoutSeconds: Number(process.env.ALLEGRO_SCRAPER_TIMEOUT_SECONDS ?? 90),
            proxies: proxiesMeta(),
            queue: taskQueue.getQueueStats(),
            logs: stats.logs,
            // Robust fallback system info
            robust: {
                enabled: robustConfig.ENABLE_ROBUST_FALLBACK,
                fallbackChain: getFallbackChainStatus(),
                concurrency: getConcurrencyStats(),
                stickySessions: getActiveSessionCount(),
                fallbackStats: stats.fallback,
            },
        }),
    );

    app.get('/proxies', (c) => c.json(proxiesMeta()));

    app.post('/proxies/reload', (c) => {
        try {
            const meta = reloadProxies();
            if (meta.count > 0) startWorkers();
            return c.json({ status: 'ok', ...meta });
        } catch (err) {
            return c.json({ status: 'error', error: (err as Error).message }, 500);
        }
    });

    /**
     * Auto-resize the proxy pool, e.g. before starting a large run.
     * Body: { sessions?: number, forEans?: number }
     *   sessions  - exact target session count
     *   forEans   - target derived from run size: ceil(forEans/15), clamped [60, 500]
     */
    app.post('/proxies/resize', async (c) => {
        try {
            const body = (await c.req.json().catch(() => ({}))) as {
                sessions?: number;
                forEans?: number;
            };
            let target: number | undefined;
            if (typeof body.sessions === 'number' && body.sessions > 0) {
                target = Math.floor(body.sessions);
            } else if (typeof body.forEans === 'number' && body.forEans > 0) {
                const EANS_PER_SESSION = 15;
                target = Math.ceil(body.forEans / EANS_PER_SESSION);
            }
            if (!target) {
                return c.json({ status: 'error', error: 'sessions or forEans required' }, 400);
            }
            target = Math.min(500, Math.max(60, target));
            const meta = reloadProxies(undefined, target);
            if (meta.count > 0) startWorkers();
            return c.json({ status: 'ok', target, ...meta });
        } catch (err) {
            return c.json({ status: 'error', error: (err as Error).message }, 500);
        }
    });

    app.get('/logs', (c) => {
        // newest first
        const logs = [...stats.logs].reverse();
        return c.json({ logs });
    });

    return app;
}
