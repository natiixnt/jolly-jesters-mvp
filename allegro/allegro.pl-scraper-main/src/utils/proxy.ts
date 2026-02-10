import fs from 'node:fs';

let proxies: URL[] = [];
let proxiesPath = 'proxies.txt';

function buildSourceList(path: string): string[] {
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

export function loadProxies(path = 'proxies.txt'): number {
    proxiesPath = path;
    const unique = buildSourceList(path);
    if (unique.length === 0) throw new Error('No proxies configured. Provide PROXIES env or a proxies file.');

    const valid: URL[] = [];
    for (const line of unique) {
        try {
            valid.push(new URL(line));
        } catch (err) {
            // ignore malformed entries
            // eslint-disable-next-line no-console
            console.error(`Invalid proxy skipped: ${line}`);
        }
    }
    if (valid.length === 0) throw new Error('No valid proxies after parsing.');
    proxies = valid;
    return proxies.length;
}

export function reloadProxies(path?: string): { count: number; path: string } {
    const usedPath = path ?? proxiesPath;
    const count = loadProxies(usedPath);
    return { count, path: usedPath };
}

export function proxiesMeta(): { count: number; path: string } {
    return { count: proxies.length, path: proxiesPath };
}

export function getRandomProxy(): URL {
    if (proxies.length === 0) throw new Error('Proxies not loaded');
    return proxies[Math.floor(Math.random() * proxies.length)];
}
