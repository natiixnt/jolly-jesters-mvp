/**
 * HTTP client using curl-impersonate for Chrome 146 TLS fingerprint.
 *
 * DataDome on Allegro blocks OpenSSL and headless browser TLS fingerprints.
 * curl-impersonate emulates real Chrome 146 TLS (BoringSSL with correct
 * cipher suites, extensions, ALPN) which passes DataDome without JS.
 *
 * Falls back to regular fetch if curl-impersonate is not installed.
 */

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';

const execFileAsync = promisify(execFile);

// Paths where curl-impersonate might be installed
const CURL_PATHS = [
    '/opt/curl-impersonate/curl_chrome146',
    '/usr/local/bin/curl_chrome146',
    '/usr/bin/curl_chrome146',
];

const LIB_PATHS = [
    '/opt/curl-impersonate',
    '/usr/local/lib',
];

let curlBinary: string | null = null;
let libPath: string | null = null;

function findCurl(): { binary: string; lib: string } | null {
    if (curlBinary && libPath) return { binary: curlBinary, lib: libPath };

    for (const p of CURL_PATHS) {
        if (fs.existsSync(p)) {
            curlBinary = p;
            break;
        }
    }
    if (!curlBinary) return null;

    for (const p of LIB_PATHS) {
        if (fs.existsSync(p)) {
            libPath = p;
            break;
        }
    }
    libPath = libPath || '/opt/curl-impersonate';

    return { binary: curlBinary, lib: libPath };
}

export interface CurlResponse {
    status: number;
    body: string;
    headers: Record<string, string>;
}

/**
 * Make an HTTP GET request using curl-impersonate with Chrome 146 fingerprint.
 *
 * @param url - URL to fetch
 * @param proxy - Full proxy URL (http://user:pass@host:port)
 * @param extraHeaders - Additional headers to send
 * @returns Response with status, body, and headers
 */
export async function curlGet(
    url: string,
    proxy?: string,
    extraHeaders?: Record<string, string>,
): Promise<CurlResponse> {
    const curl = findCurl();
    if (!curl) {
        throw new Error('curl-impersonate not found. Install to /opt/curl-impersonate/');
    }

    const args: string[] = [
        '-s',           // silent
        '-D', '-',      // dump headers to stdout (we'll parse them)
        '-L',           // follow redirects
        '--max-time', '15',
        '-H', 'Accept-Language: pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
    ];

    if (proxy) {
        args.push('-x', proxy);
    }

    if (extraHeaders) {
        for (const [key, value] of Object.entries(extraHeaders)) {
            args.push('-H', `${key}: ${value}`);
        }
    }

    args.push(url);

    const { stdout } = await execFileAsync(curl.binary, args, {
        env: { ...process.env, LD_LIBRARY_PATH: curl.lib },
        maxBuffer: 20 * 1024 * 1024, // 20MB
        timeout: 20_000,
    });

    // Parse response: headers are before \r\n\r\n, body after
    const headerEnd = stdout.indexOf('\r\n\r\n');
    if (headerEnd === -1) {
        // No headers separator - might be just body
        return { status: 0, body: stdout, headers: {} };
    }

    const headerBlock = stdout.substring(0, headerEnd);
    const body = stdout.substring(headerEnd + 4);

    // Parse status from first line (might have multiple due to redirects)
    const headerLines = headerBlock.split('\r\n');
    let status = 0;
    const headers: Record<string, string> = {};

    for (const line of headerLines) {
        if (line.startsWith('HTTP/')) {
            const parts = line.split(' ');
            status = parseInt(parts[1] || '0', 10);
        } else if (line.includes(':')) {
            const [key, ...rest] = line.split(':');
            headers[key.trim().toLowerCase()] = rest.join(':').trim();
        }
    }

    return { status, body, headers };
}

/**
 * Run multiple curl requests in parallel.
 * Each request gets a different proxy from the provided list.
 *
 * @param requests - Array of {url, proxy} pairs
 * @param maxConcurrent - Max parallel curl processes (default 10)
 * @returns Array of results (same order as requests)
 */
export async function curlGetParallel(
    requests: Array<{ url: string; proxy: string }>,
    maxConcurrent = 10,
): Promise<CurlResponse[]> {
    const results: CurlResponse[] = [];

    // Process in chunks of maxConcurrent
    for (let i = 0; i < requests.length; i += maxConcurrent) {
        const chunk = requests.slice(i, i + maxConcurrent);
        const chunkResults = await Promise.allSettled(
            chunk.map(({ url, proxy }) => curlGet(url, proxy))
        );
        for (const r of chunkResults) {
            if (r.status === 'fulfilled') {
                results.push(r.value);
            } else {
                results.push({ status: 0, body: '', headers: {} });
            }
        }
    }

    return results;
}

/**
 * Check if curl-impersonate is available.
 */
export function isCurlAvailable(): boolean {
    return findCurl() !== null;
}
