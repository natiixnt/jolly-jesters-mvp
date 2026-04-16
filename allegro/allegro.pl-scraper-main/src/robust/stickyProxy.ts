/**
 * Sticky Proxy Manager.
 *
 * Manages persistent proxy sessions for browser-based strategies.
 * Supports two proxy types:
 *   - Sticky residential (Decodo/Smartproxy/Oxylabs) with 30-60 min sessions
 *   - Mobile 4G/LTE with 60+ min sessions (last resort)
 *
 * Proxy endpoint format (Decodo example):
 *   socks5://user-sessid_XXXX:password@gate.decodo.com:7000
 *
 * The session ID is appended to the username to create sticky sessions.
 * Each EAN+strategy combination gets its own session for isolation.
 */

import { createHash, randomBytes } from 'node:crypto';
import { robustConfig } from './config';
import type { ProxyType } from './types';

// ---------------------------------------------------------------------------
// Session store: sessionKey -> { proxyUrl, createdAt, type }
// ---------------------------------------------------------------------------

interface StickySession {
    proxyUrl: string;
    sessionId: string;
    proxyType: ProxyType;
    createdAt: number;
    ttlMs: number;
}

const sessions = new Map<string, StickySession>();

// Cleanup stale sessions every 5 minutes
setInterval(() => {
    const now = Date.now();
    for (const [key, session] of sessions) {
        if (now - session.createdAt > session.ttlMs) {
            sessions.delete(key);
        }
    }
}, 5 * 60 * 1000);

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Get or create a sticky proxy session.
 *
 * @param ean - Product EAN (used as part of session key)
 * @param type - Proxy type: 'sticky' (residential) or 'mobile'
 * @param hint - Optional extra key component (e.g. run ID)
 * @returns Object with proxyUrl (full URL with session), sessionId, proxyType
 */
export function getStickyProxy(
    ean: string,
    type: 'sticky' | 'mobile',
    hint?: string,
): { proxyUrl: string; sessionId: string; proxyType: ProxyType } {
    const sessionKey = `${type}:${ean}:${hint ?? 'default'}`;

    // Return existing session if still alive
    const existing = sessions.get(sessionKey);
    if (existing && Date.now() - existing.createdAt < existing.ttlMs) {
        return {
            proxyUrl: existing.proxyUrl,
            sessionId: existing.sessionId,
            proxyType: existing.proxyType,
        };
    }

    // Create new session
    const cfg = robustConfig.STICKY_PROXY;
    const endpoint = type === 'mobile' ? cfg.mobile_endpoint : cfg.residential_endpoint;
    const ttlMin = type === 'mobile' ? cfg.mobile_session_ttl_min : cfg.residential_session_ttl_min;

    if (!endpoint) {
        throw new Error(
            `Sticky proxy endpoint not configured for type="${type}". ` +
            `Set STICKY_PROXY_${type === 'mobile' ? 'MOBILE' : 'RESIDENTIAL'}_ENDPOINT.`,
        );
    }

    // Generate a unique session ID (8 hex chars - short but unique enough)
    const sessionId = randomBytes(4).toString('hex');

    // Build the proxy URL with session ID injected into the username
    // Format: protocol://username-session_XXXX:password@host:port
    const proxyUrl = buildStickyUrl(endpoint, sessionId, cfg.proxy_username, cfg.proxy_password);
    const proxyType: ProxyType = type === 'mobile' ? 'mobile' : 'sticky';

    const session: StickySession = {
        proxyUrl,
        sessionId,
        proxyType,
        createdAt: Date.now(),
        ttlMs: ttlMin * 60 * 1000,
    };
    sessions.set(sessionKey, session);

    return { proxyUrl, sessionId, proxyType };
}

/**
 * Invalidate a sticky session (e.g. after a severe block).
 * Next call to getStickyProxy with the same key will create a new session.
 */
export function invalidateStickySession(ean: string, type: 'sticky' | 'mobile', hint?: string): void {
    const sessionKey = `${type}:${ean}:${hint ?? 'default'}`;
    sessions.delete(sessionKey);
}

/**
 * Get count of active sessions (for monitoring).
 */
export function getActiveSessionCount(): { residential: number; mobile: number; total: number } {
    let residential = 0;
    let mobile = 0;
    const now = Date.now();
    for (const session of sessions.values()) {
        if (now - session.createdAt > session.ttlMs) continue;
        if (session.proxyType === 'mobile') mobile++;
        else residential++;
    }
    return { residential, mobile, total: residential + mobile };
}

/**
 * Hash a proxy URL for safe logging/metrics (same logic as main proxy.ts).
 */
export function stickyProxyHash(url: string): string {
    return createHash('sha256').update(url).digest('hex').slice(0, 16);
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

/**
 * Build a sticky proxy URL by injecting a session ID into the endpoint.
 *
 * Supports multiple formats:
 *   1. Template: "socks5://user-session_{SESSION}:pass@host:port"
 *      -> replaces {SESSION} with the session ID
 *   2. Plain URL: "socks5://host:port"
 *      -> injects "username-session_XXXX:password@" before host
 *   3. URL with auth: "socks5://user:pass@host:port"
 *      -> appends "-session_XXXX" to the username
 */
function buildStickyUrl(
    endpoint: string,
    sessionId: string,
    username: string,
    password: string,
): string {
    // Template mode: replace {SESSION} placeholder
    if (endpoint.includes('{SESSION}')) {
        return endpoint.replace('{SESSION}', sessionId);
    }

    try {
        const url = new URL(endpoint);

        if (url.username) {
            // Endpoint already has auth - append session to username
            url.username = `${url.username}-session_${sessionId}`;
        } else if (username) {
            // Use configured credentials with session
            url.username = `${username}-session_${sessionId}`;
            url.password = password;
        } else {
            // No auth at all - just use session as username
            url.username = `session_${sessionId}`;
        }

        return url.toString();
    } catch {
        // Fallback: treat as raw string, append session
        return endpoint.replace('://', `://${username}-session_${sessionId}:${password}@`);
    }
}
