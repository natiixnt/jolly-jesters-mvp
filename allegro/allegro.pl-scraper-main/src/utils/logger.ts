import fs from 'node:fs';
import path from 'node:path';
import { config } from '@/config';
import type { Stats, LogEntry } from '@/utils/stats';

export class ScopedLogger {
    constructor(
        private prefix: string,
        private logger: Logger,
    ) {}

    log(...args: unknown[]): void {
        this.logger.write(this.prefix, args.map(String).join(' '));
    }

    activity(message: string, type: LogEntry['type']): void {
        this.logger.write(this.prefix, message, {
            time: Date.now(),
            workerId: this.prefix,
            message,
            type,
        });
    }

    scoped(childPrefix: string): ScopedLogger {
        return new ScopedLogger(`${this.prefix}/${childPrefix}`, this.logger);
    }
}

export class Logger {
    private stream: fs.WriteStream | null = null;
    private stats: Stats;

    constructor(stats: Stats) {
        this.stats = stats;

        if (config.DEBUG) {
            const logDir = path.join(process.cwd(), 'logs');
            fs.mkdirSync(logDir, { recursive: true });
            this.stream = fs.createWriteStream(path.join(logDir, 'debug.log'), { flags: 'a' });
        }
    }

    scoped(prefix: string): ScopedLogger {
        return new ScopedLogger(prefix, this);
    }

    write(prefix: string, message: string, dashboardEntry?: LogEntry): void {
        if (this.stream) {
            const timestamp = new Date().toISOString();
            this.stream.write(`${timestamp} [${prefix}] ${message}\n`);
        }
        if (dashboardEntry) {
            this.stats.pushLog(dashboardEntry);
        }
    }

    destroy(): void {
        this.stream?.end();
    }
}
