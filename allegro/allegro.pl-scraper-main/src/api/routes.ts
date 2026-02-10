import { Hono } from 'hono';
import { config } from '@/config';
import type { TaskQueue } from '@/queue/taskQueue';
import type { CreateTaskBody, TaskResponse } from '@/types';
import { proxiesMeta, reloadProxies } from '@/utils/proxy';
import type { Stats } from '@/utils/stats';

export function createRoutes(taskQueue: TaskQueue, startWorkers: () => void, stats: Stats): Hono {
    const app = new Hono();

    app.post('/createTask', async (c) => {
        const body = await c.req.json<CreateTaskBody>();
        if (!body.ean || !/^\d{8,13}$/.test(body.ean)) {
            return c.json({ error: 'Invalid EAN (8-13 digits required)' }, 400);
        }
        const task = taskQueue.createTask(body.ean);
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
            logs: stats.logs,
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

    app.get('/logs', (c) => {
        // newest first
        const logs = [...stats.logs].reverse();
        return c.json({ logs });
    });

    return app;
}
