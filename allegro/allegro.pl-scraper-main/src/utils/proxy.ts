import fs from 'node:fs';

let proxies: URL[] = [];

export function loadProxies(path = 'proxies.txt'): void {
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
    const unique = Array.from(new Set(sources));
    if (unique.length === 0) throw new Error('No proxies configured. Set PROXIES env or provide proxies.txt');
    proxies = unique.map((l) => new URL(l));
}

export function getRandomProxy(): URL {
    if (proxies.length === 0) throw new Error('Proxies not loaded');
    return proxies[Math.floor(Math.random() * proxies.length)];
}
