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

loadProxies();

const stats = new Stats();
const logger = new Logger(stats);
const serverLog = logger.scoped('Server');

const taskQueue = new TaskQueue();
stats.taskQueue = taskQueue;

const pool = new WorkerPool(taskQueue, stats, logger);
const app = createRoutes(taskQueue);

pool.start();

render(React.createElement(Dashboard, { stats }));

serve({ fetch: app.fetch, port: config.PORT }, (info) => {
    serverLog.log(`Listening on http://localhost:${String(info.port)}`);
});
