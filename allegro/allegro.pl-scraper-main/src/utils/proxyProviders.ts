/**
 * Proxy provider abstraction.
 *
 * Generates sticky-session proxy URLs from credentials in env vars,
 * eliminating the need to maintain a separate proxy list file or
 * manually generate sessions in a provider's panel.
 *
 * Each provider produces N "sticky sessions" - dedicated session-IDs
 * that map to consistent IPs (within the provider's pool) for the
 * lifetime of the session. Session IDs are random (10-char hex), so
 * each rebuild of the pool gives an entirely fresh set of IPs.
 *
 * To add a new provider: implement ProxyProvider interface and add to PROVIDERS.
 */

import { randomBytes } from 'node:crypto';

export interface ProxyProvider {
    name: string;
    enabled: boolean;
    sessionCount: number;
    buildUrl(sessionId: string): string;
}

function randomSessionId(): string {
    return randomBytes(5).toString('hex').toUpperCase();
}

class EvomiProvider implements ProxyProvider {
    name = 'evomi';

    get enabled(): boolean {
        return !!(process.env.EVOMI_USERNAME && process.env.EVOMI_PASSWORD);
    }

    get sessionCount(): number {
        return Math.max(1, parseInt(process.env.EVOMI_SESSIONS || '60', 10));
    }

    /**
     * Evomi sticky-session format places session-ID inside the password,
     * not the username:
     *   user: biurojoll7
     *   pass: BASE_country-PL_session-XYZ123
     * Generating a fresh session-ID gives a fresh sticky IP from Evomi's pool.
     */
    buildUrl(sessionId: string): string {
        const user = process.env.EVOMI_USERNAME!;
        const basePass = process.env.EVOMI_PASSWORD!;
        const endpoint = process.env.EVOMI_ENDPOINT || 'core-residential.evomi.com:1000';
        const country = (process.env.EVOMI_COUNTRY || 'PL').toUpperCase();
        const fullPass = `${basePass}_country-${country}_session-${sessionId}`;
        return `http://${encodeURIComponent(user)}:${encodeURIComponent(fullPass)}@${endpoint}`;
    }
}

class IPRoyalProvider implements ProxyProvider {
    name = 'iproyal';

    get enabled(): boolean {
        return !!(process.env.IPROYAL_USERNAME && process.env.IPROYAL_PASSWORD);
    }

    get sessionCount(): number {
        return Math.max(1, parseInt(process.env.IPROYAL_SESSIONS || '60', 10));
    }

    /**
     * IPRoyal sticky-session format places session-ID inside the username:
     *   user: USER_country-pl_session-XYZ123_lifetime-30m
     *   pass: PASSWORD
     */
    buildUrl(sessionId: string): string {
        const user = process.env.IPROYAL_USERNAME!;
        const pass = process.env.IPROYAL_PASSWORD!;
        const endpoint = process.env.IPROYAL_ENDPOINT || 'geo.iproyal.com:12321';
        const country = (process.env.IPROYAL_COUNTRY || 'pl').toLowerCase();
        const username = `${user}_country-${country}_session-${sessionId}_lifetime-30m`;
        return `http://${encodeURIComponent(username)}:${encodeURIComponent(pass)}@${endpoint}`;
    }
}

const PROVIDERS: ProxyProvider[] = [new EvomiProvider(), new IPRoyalProvider()];

export interface BuiltProxy {
    url: URL;
    provider: string;
    sessionId: string;
    sessionKey: string; // unique identifier "provider:sessionId" for quarantine etc.
}

/**
 * Build a fresh proxy pool from configured providers, with newly randomized
 * session-IDs. Each call returns a completely new set of sticky IPs.
 */
export function buildProxyPool(): BuiltProxy[] {
    const enabled = PROVIDERS.filter((p) => p.enabled);
    if (enabled.length === 0) return [];

    const pool: BuiltProxy[] = [];
    for (const provider of enabled) {
        for (let i = 0; i < provider.sessionCount; i++) {
            const sessionId = randomSessionId();
            const url = new URL(provider.buildUrl(sessionId));
            pool.push({
                url,
                provider: provider.name,
                sessionId,
                sessionKey: `${provider.name}:${sessionId}`,
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
