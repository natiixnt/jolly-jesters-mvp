import React from 'react';
import { render } from 'ink';
import { serve } from '@hono/node-server';
import { config } from '@/config';
import { loadProxies } from '@/utils/proxy';
import { TaskQueue } from '@/queue/taskQueue';
import { WorkerPool } from '@/worker/workerPool';
import { createRoutes } from '@/api/routes';
import { Stats } from '@/utils/stats';
import { Logger } from '@/utils/logger';
import Dashboard from '@/utils/dashboard';

const proxiesFile = process.env.PROXIES_FILE ?? 'proxies.txt';
const stats = new Stats();
const logger = new Logger(stats);
const serverLog = logger.scoped('Server');
const taskQueue = new TaskQueue();
stats.taskQueue = taskQueue;

let pool: WorkerPool | null = null;

function startWorkers(countHint?: number): void {
    if (pool) return;
    if (countHint !== undefined && countHint <= 0) return;
    try {
        pool = new WorkerPool(taskQueue, stats, logger);
        pool.start();
        serverLog.log('Worker pool started');
    } catch (err) {
        serverLog.log(`Worker pool not started: ${(err as Error).message}`);
        pool = null;
    }
}

try {
    const count = loadProxies(proxiesFile);
    serverLog.log(`Loaded ${count} proxies from ${proxiesFile}`);
    if (count > 0) startWorkers(count);
} catch (err) {
    serverLog.log(`Proxy load failed: ${(err as Error).message}. Upload a file and POST /proxies/reload.`);
}

const app = createRoutes(taskQueue, () => startWorkers(), stats);

render(React.createElement(Dashboard, { stats }));

serve({ fetch: app.fetch, port: config.PORT }, (info) => {
    serverLog.log(`Listening on http://localhost:${String(info.port)}`);
});
