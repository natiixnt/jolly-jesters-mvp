import fs from 'node:fs';
import { createHash } from 'node:crypto';

import { BuiltProxy, buildProxyPool, providersSummary } from './proxyProviders';

interface ProxyEntry {
    url: URL;
    provider: string;
    sessionKey: string;
    quarantineUntil: number; // epoch ms; 0 = active
    consecutiveFailures: number;
    failureTimestamps: number[]; // last failures (rolling window for hour-rate)
}

let entries: ProxyEntry[] = [];
let proxiesPath = '';
let rrIndex = 0;

const QUARANTINE_BURST_THRESHOLD = 3; // 3 consecutive failures -> short quarantine
const QUARANTINE_BURST_DURATION_MS = 30 * 60 * 1000; // 30 min
const QUARANTINE_HOURLY_THRESHOLD = 5; // 5 failures in 60 min -> longer quarantine
const QUARANTINE_HOURLY_DURATION_MS = 60 * 60 * 1000; // 1h
const HOURLY_WINDOW_MS = 60 * 60 * 1000;

function entryFromBuilt(b: BuiltProxy): ProxyEntry {
    return {
        url: b.url,
        provider: b.provider,
        sessionKey: b.sessionKey,
        quarantineUntil: 0,
        consecutiveFailures: 0,
        failureTimestamps: [],
    };
}

function entryFromLegacyUrl(line: string): ProxyEntry | null {
    try {
        const url = new URL(line);
        return {
            url,
            provider: 'legacy',
            sessionKey: `legacy:${proxyUrlHash(url)}`,
            quarantineUntil: 0,
            consecutiveFailures: 0,
            failureTimestamps: [],
        };
    } catch {
        return null;
    }
}

function buildLegacySources(path: string): string[] {
    const sources: string[] = [];
    const envList = process.env.PROXIES || process.env.PROXY_LIST;
    if (envList) {
        sources.push(
            ...envList
                .split(',')
                .map((l) => l.trim())
                .filter(Boolean),
        );
    }
    if (fs.existsSync(path)) {
        const fileLines = fs
            .readFileSync(path, 'utf-8')
            .split('\n')
            .map((l) => l.trim())
            .filter(Boolean);
        sources.push(...fileLines);
    }
    return Array.from(new Set(sources));
}

/**
 * Load proxies from configured providers (preferred) or legacy file/env (fallback).
 * Returns the count of loaded proxy entries.
 */
export function loadProxies(path = 'proxies.txt'): number {
    proxiesPath = path;

    // Preferred: provider-based config
    const built = buildProxyPool();
    if (built.length > 0) {
        entries = built.map(entryFromBuilt);
        return entries.length;
    }

    // Fallback: legacy proxies.txt / PROXIES env
    const lines = buildLegacySources(path);
    const valid = lines
        .map((line) => entryFromLegacyUrl(line))
        .filter((e): e is ProxyEntry => e !== null);
    if (valid.length === 0) {
        throw new Error(
            'No proxies configured. Set EVOMI_USERNAME/PASSWORD (or IPROYAL_*) in env, or provide a legacy proxies file.',
        );
    }
    entries = valid;
    return entries.length;
}

export function reloadProxies(path?: string): { count: number; path: string } {
    const usedPath = path ?? proxiesPath;
    const count = loadProxies(usedPath);
    rrIndex = Math.floor(Math.random() * Math.max(1, entries.length));
    return { count, path: usedPath };
}

export function proxiesMeta(): {
    count: number;
    activeCount: number;
    quarantinedCount: number;
    path: string;
    providers: ReturnType<typeof providersSummary>;
} {
    const now = Date.now();
    const activeCount = entries.filter((e) => e.quarantineUntil <= now).length;
    return {
        count: entries.length,
        activeCount,
        quarantinedCount: entries.length - activeCount,
        path: proxiesPath,
        providers: providersSummary(),
    };
}

function isAvailable(entry: ProxyEntry, now: number): boolean {
    return entry.quarantineUntil <= now;
}

export function getRandomProxy(): URL {
    if (entries.length === 0) throw new Error('Proxies not loaded');
    const now = Date.now();
    const available = entries.filter((e) => isAvailable(e, now));
    const pool = available.length > 0 ? available : entries; // fallback if all in quarantine
    return pool[Math.floor(Math.random() * pool.length)].url;
}

export function proxyCount(): number {
    return entries.length;
}

export function activeProxyCount(): number {
    const now = Date.now();
    return entries.filter((e) => isAvailable(e, now)).length;
}

export function proxyUrlHash(url: URL | string): string {
    const str = typeof url === 'string' ? url : url.toString();
    return createHash('sha256').update(str).digest('hex').slice(0, 16);
}

/**
 * Round-robin selection of next proxy, preferring active (non-quarantined) entries.
 * `exclude` may be a Set of proxy URL strings or a single URL to skip.
 */
export function nextProxy(exclude?: Set<string> | URL): URL {
    if (entries.length === 0) throw new Error('Proxies not loaded');
    if (entries.length === 1) return entries[0].url;

    const excludeSet =
        exclude instanceof Set
            ? exclude
            : exclude
              ? new Set<string>([exclude.toString()])
              : new Set<string>();

    const now = Date.now();
    let tries = 0;
    while (tries < entries.length) {
        const candidate = entries[rrIndex % entries.length];
        rrIndex = (rrIndex + 1) % entries.length;
        const urlStr = candidate.url.toString();
        if (!excludeSet.has(urlStr) && isAvailable(candidate, now)) {
            return candidate.url;
        }
        tries++;
    }
    // All entries excluded or quarantined - return any non-excluded fallback
    for (const e of entries) {
        if (!excludeSet.has(e.url.toString())) return e.url;
    }
    return entries[0].url;
}

function findEntryByUrl(url: URL | string): ProxyEntry | null {
    const target = typeof url === 'string' ? url : url.toString();
    return entries.find((e) => e.url.toString() === target) ?? null;
}

/**
 * Record a successful request through a proxy. Resets failure counters.
 */
export function recordProxySuccess(url: URL | string): void {
    const entry = findEntryByUrl(url);
    if (!entry) return;
    entry.consecutiveFailures = 0;
    // Drop failure timestamps older than 1 hour (rolling window)
    const cutoff = Date.now() - HOURLY_WINDOW_MS;
    entry.failureTimestamps = entry.failureTimestamps.filter((t) => t >= cutoff);
}

/**
 * Record a failed request through a proxy. May trigger quarantine.
 * Returns true if the proxy got quarantined as a result.
 */
export function recordProxyFailure(url: URL | string, reason?: string): boolean {
    const entry = findEntryByUrl(url);
    if (!entry) return false;
    const now = Date.now();
    entry.consecutiveFailures += 1;
    entry.failureTimestamps.push(now);
    const cutoff = now - HOURLY_WINDOW_MS;
    entry.failureTimestamps = entry.failureTimestamps.filter((t) => t >= cutoff);

    // Quarantine logic
    if (entry.consecutiveFailures >= QUARANTINE_BURST_THRESHOLD) {
        entry.quarantineUntil = now + QUARANTINE_BURST_DURATION_MS;
        return true;
    }
    if (entry.failureTimestamps.length >= QUARANTINE_HOURLY_THRESHOLD) {
        entry.quarantineUntil = now + QUARANTINE_HOURLY_DURATION_MS;
        return true;
    }
    return false;
}

/**
 * Lift quarantine from a proxy (manual or after successful health-check).
 */
export function clearProxyQuarantine(url: URL | string): void {
    const entry = findEntryByUrl(url);
    if (!entry) return;
    entry.quarantineUntil = 0;
    entry.consecutiveFailures = 0;
}

export function getQuarantinedEntries(): Array<{ provider: string; sessionKey: string; quarantineUntil: number }> {
    const now = Date.now();
    return entries
        .filter((e) => e.quarantineUntil > now)
        .map((e) => ({
            provider: e.provider,
            sessionKey: e.sessionKey,
            quarantineUntil: e.quarantineUntil,
        }));
}

/**
 * Iterate over all entries currently in quarantine and lift if quarantine expired.
 * Called by scheduled health-check.
 */
export function expireQuarantines(): number {
    const now = Date.now();
    let lifted = 0;
    for (const entry of entries) {
        if (entry.quarantineUntil > 0 && entry.quarantineUntil <= now) {
            entry.quarantineUntil = 0;
            entry.consecutiveFailures = 0;
            lifted++;
        }
    }
    return lifted;
}
