/**
 * Proxy provider abstraction.
 *
 * Generates sticky-session proxy URLs from credentials in env vars,
 * eliminating the need to maintain a separate proxy list file.
 *
 * Each provider produces N "sticky sessions" - dedicated session-IDs
 * that map to consistent IPs (within the provider's pool) for the
 * lifetime of the session. Rotating across sessions = rotating across IPs.
 *
 * To add a new provider: implement ProxyProvider interface and add to PROVIDERS.
 */

export interface ProxyProvider {
    name: string;
    enabled: boolean;
    sessionCount: number;
    buildUrl(sessionId: number): string;
}

class EvomiProvider implements ProxyProvider {
    name = 'evomi';

    get enabled(): boolean {
        return !!(process.env.EVOMI_USERNAME && process.env.EVOMI_PASSWORD);
    }

    get sessionCount(): number {
        return Math.max(1, parseInt(process.env.EVOMI_SESSIONS || '30', 10));
    }

    buildUrl(sessionId: number): string {
        const user = process.env.EVOMI_USERNAME!;
        const pass = process.env.EVOMI_PASSWORD!;
        const endpoint = process.env.EVOMI_ENDPOINT || 'core-residential.evomi.com:1000';
        const country = process.env.EVOMI_COUNTRY || 'PL';
        // Evomi sticky session format: user-country-XX-session-N
        const username = `${user}-country-${country}-session-${sessionId}`;
        // URL-encode password (may contain : @ , etc.)
        const encodedPass = encodeURIComponent(pass);
        return `http://${username}:${encodedPass}@${endpoint}`;
    }
}

class IPRoyalProvider implements ProxyProvider {
    name = 'iproyal';

    get enabled(): boolean {
        return !!(process.env.IPROYAL_USERNAME && process.env.IPROYAL_PASSWORD);
    }

    get sessionCount(): number {
        return Math.max(1, parseInt(process.env.IPROYAL_SESSIONS || '30', 10));
    }

    buildUrl(sessionId: number): string {
        const user = process.env.IPROYAL_USERNAME!;
        const pass = process.env.IPROYAL_PASSWORD!;
        const endpoint = process.env.IPROYAL_ENDPOINT || 'geo.iproyal.com:12321';
        const country = (process.env.IPROYAL_COUNTRY || 'pl').toLowerCase();
        // IPRoyal sticky format: user_country-pl_session-XYZ_lifetime-30m
        const username = `${user}_country-${country}_session-${sessionId}_lifetime-30m`;
        const encodedPass = encodeURIComponent(pass);
        return `http://${username}:${encodedPass}@${endpoint}`;
    }
}

const PROVIDERS: ProxyProvider[] = [new EvomiProvider(), new IPRoyalProvider()];

export interface BuiltProxy {
    url: URL;
    provider: string;
    sessionId: number;
    sessionKey: string; // unique identifier "provider:sessionId" for quarantine etc.
}

/**
 * Build the proxy pool from configured providers.
 *
 * Returns array of BuiltProxy objects, each tagged with provider name and session id
 * so quarantine logic can operate on session granularity.
 */
export function buildProxyPool(): BuiltProxy[] {
    const enabled = PROVIDERS.filter((p) => p.enabled);
    if (enabled.length === 0) return [];

    const pool: BuiltProxy[] = [];
    for (const provider of enabled) {
        for (let i = 0; i < provider.sessionCount; i++) {
            const url = new URL(provider.buildUrl(i));
            pool.push({
                url,
                provider: provider.name,
                sessionId: i,
                sessionKey: `${provider.name}:${i}`,
            });
        }
    }
    return pool;
}

/** Summary of configured providers (for logs and /health endpoint). */
export function providersSummary(): Array<{ name: string; enabled: boolean; sessions: number }> {
    return PROVIDERS.map((p) => ({
        name: p.name,
        enabled: p.enabled,
        sessions: p.enabled ? p.sessionCount : 0,
    }));
}
