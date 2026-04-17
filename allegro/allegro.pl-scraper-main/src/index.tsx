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
import { robustConfig, initFallbackChain, destroyFallbackChain } from '@/robust';

const proxiesFile = process.env.PROXIES_FILE ?? 'proxies.txt';
const stats = new Stats();
const logger = new Logger(stats);
const serverLog = logger.scoped('Server');
const taskQueue = new TaskQueue(config.MAX_PENDING_TASKS);
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

// Initialize the robust fallback chain (if enabled)
if (robustConfig.ENABLE_ROBUST_FALLBACK) {
    serverLog.log('Robust fallback system ENABLED - initializing strategies...');
    initFallbackChain(logger.scoped('Robust')).then(() => {
        serverLog.log(`Fallback levels: ${robustConfig.FALLBACK_LEVELS.filter((l) => l.enabled).map((l) => l.name).join(' -> ')}`);
    }).catch((err) => {
        serverLog.log(`Fallback init failed: ${(err as Error).message}`);
    });
} else {
    serverLog.log('Robust fallback system disabled (set ENABLE_ROBUST_FALLBACK=true to enable)');
}

// Graceful shutdown
process.on('SIGTERM', async () => {
    serverLog.log('SIGTERM received, shutting down...');
    await destroyFallbackChain();
    process.exit(0);
});

const app = createRoutes(taskQueue, () => startWorkers(), stats);

render(React.createElement(Dashboard, { stats }));

serve({ fetch: app.fetch, port: config.PORT }, (info) => {
    serverLog.log(`Listening on http://localhost:${String(info.port)}`);
});
