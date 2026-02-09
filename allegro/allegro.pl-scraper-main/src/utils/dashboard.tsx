import React, { useState, useEffect } from 'react';
import { Box, Text } from 'ink';
import Table from 'ink-table';
import { config } from '@/config';
import { LOG_RING_SIZE } from '@/utils/stats';
import type { Stats, WorkerState, LogEntry } from '@/utils/stats';

function fmtMs(ms: number): string {
    if (!Number.isFinite(ms) || ms === 0) return '—';
    if (ms < 1000) return `${ms.toFixed(0)}ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60_000).toFixed(1)}m`;
}

function fmtUptime(startedAt: number): string {
    const sec = Math.floor((Date.now() - startedAt) / 1000);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${String(h)}h ${String(m).padStart(2, '0')}m`;
    if (m > 0) return `${String(m)}m ${String(s).padStart(2, '0')}s`;
    return `${String(s)}s`;
}

function fmtSessionAge(startedAt: number | null): string {
    return startedAt === null ? '—' : fmtUptime(startedAt);
}

function fmtTime(ts: number): string {
    const d = new Date(ts);
    return [d.getHours(), d.getMinutes(), d.getSeconds()]
        .map((n) => String(n).padStart(2, '0'))
        .join(':');
}

function fmtCount(n: number): string {
    if (n === 0) return '—';
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
}

function Section({ title, color, children }: { title?: string; color?: string; children: React.ReactNode }) {
    return (
        <Box flexDirection="column" borderStyle="round" borderColor={color ?? 'gray'} paddingX={1}>
            {title && (
                <Text bold color={color ?? 'white'}>
                    {title}
                </Text>
            )}
            {children}
        </Box>
    );
}

function Stat({ label, value, color }: { label: string; value: string | number; color?: string }) {
    return (
        <Box gap={1}>
            <Text dimColor>{label}</Text>
            <Text bold color={color}>{String(value)}</Text>
        </Box>
    );
}

function Badge({ label, value, color }: { label: string; value: number; color: string }) {
    return (
        <Text>
            <Text dimColor>{label} </Text>
            <Text bold color={color}>{String(value)}</Text>
        </Text>
    );
}

function MinAvgMax({ label, min, avg, max }: { label: string; min: string; avg: string; max: string }) {
    return (
        <Box>
            <Text>{label.padEnd(14)}</Text>
            <Text color="green">{min.padEnd(8)}</Text>
            <Text color="yellow">{avg.padEnd(8)}</Text>
            <Text color="red">{max}</Text>
        </Box>
    );
}

function ActivityLine({ entry }: { entry: LogEntry }) {
    const icons = { success: '✓', error: '✗', info: '→' } as const;
    const colors = { success: 'green', error: 'red', info: 'blue' } as const;
    return (
        <Box gap={1}>
            <Box flexShrink={0}>
                <Text dimColor>{fmtTime(entry.time)}</Text>
            </Box>
            <Box flexShrink={0}>
                <Text color="cyan">{entry.workerId.padEnd(4)}</Text>
            </Box>
            <Box flexGrow={1}>
                <Text wrap="wrap" color={colors[entry.type]}>
                    {icons[entry.type]} {entry.message}
                </Text>
            </Box>
        </Box>
    );
}

function statusLabel(status: WorkerState['status']): string {
    switch (status) {
        case 'active':
            return '● active';
        case 'solving':
            return '◉ solving';
        case 'resetting':
            return '↻ reset';
        default:
            return '○ idle';
    }
}

export default function Dashboard({ stats }: { stats: Stats }) {
    const [, setTick] = useState(0);

    useEffect(() => {
        const timer = setInterval(() => setTick((t) => t + 1), 500);
        return () => clearInterval(timer);
    }, []);

    const q = stats.taskQueue?.getQueueStats() ?? { pending: 0, processing: 0 };
    const workers = Array.from(stats.workers.values());
    const dur = stats.taskDuration;
    const sDur = stats.sessionDuration;
    const sScrapes = stats.sessionScrapes;

    return (
        <Box flexDirection="column">
            <Box borderStyle="round" borderColor="cyan" paddingX={1} justifyContent="space-between">
                <Box flexShrink={0}>
                    <Text bold color="cyan">ALLEGRO SCRAPER</Text>
                </Box>
                <Box flexShrink={0}>
                    <Text dimColor>{fmtUptime(stats.startedAt)}</Text>
                </Box>
            </Box>

            <Box paddingX={1} gap={3}>
                <Badge label="📋 Pending" value={q.pending} color="yellow" />
                <Badge label="⚙ Processing" value={q.processing} color="cyan" />
                <Badge label="✓ Completed" value={stats.totalCompleted} color="green" />
                <Badge label="✗ Failed" value={stats.totalFailed} color="red" />
            </Box>

            <Section title="Workers" color="blue">
                {workers.length > 0 ? (
                    <Table
                        data={workers.map((w) => ({
                            Worker: w.id,
                            Status: statusLabel(w.status),
                            Tasks: `${String(w.activeTasks)}/${String(config.CONCURRENCY_PER_WORKER)}`,
                            Session: fmtSessionAge(w.sessionStartedAt),
                            Scrapes: fmtCount(w.sessionScrapes),
                            Resets: String(w.sessionResets),
                        }))}
                        columns={['Worker', 'Status', 'Tasks', 'Session', 'Scrapes', 'Resets']}
                    />
                ) : (
                    <Text dimColor>No workers registered</Text>
                )}
            </Section>

            <Box gap={1}>
                <Box flexGrow={1} flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1} height={6}>
                    <Text bold color="white">Performance</Text>
                    <Text dimColor>{'              min     avg     max'}</Text>
                    <MinAvgMax label="Task duration" min={fmtMs(dur.min)} avg={fmtMs(dur.avg)} max={fmtMs(dur.max)} />
                    <Box gap={1}>
                        <Stat label="Captchas:" value={stats.totalCaptchaSolves} color="yellow" />
                        <Stat label="Task/h:" value={stats.getTasksPerHour()} color="green" />
                    </Box>
                </Box>

                <Box flexGrow={1} flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1} height={6}>
                    <Text bold color="white">Session Analytics</Text>
                    <Text dimColor>{'              min     avg     max'}</Text>
                    <MinAvgMax label="Duration" min={fmtMs(sDur.min)} avg={fmtMs(sDur.avg)} max={fmtMs(sDur.max)} />
                    <Box gap={1}>
                        <Stat label="Scrapes/sess:" value={sScrapes.count === 0 ? '—' : String(Math.round(sScrapes.avg))} color="cyan" />
                        <Stat label="Resets:" value={stats.totalSessionResets} color="red" />
                    </Box>
                </Box>
            </Box>

            <Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1} height={LOG_RING_SIZE + 3}>
                <Text bold color="gray">Recent Activity</Text>
                {stats.logs.length === 0 ? (
                    <Text dimColor italic>Waiting for activity…</Text>
                ) : (
                    stats.logs.map((entry, i) => <ActivityLine key={i} entry={entry} />)
                )}
            </Box>

            {config.DEBUG && (
                <Box paddingX={1}>
                    <Text dimColor>📄 Logs → logs/debug.log</Text>
                </Box>
            )}
        </Box>
    );
}
