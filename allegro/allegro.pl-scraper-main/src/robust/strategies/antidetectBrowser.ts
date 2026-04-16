/**
 * Level 2 Fallback: Antidetect Browser (Camoufox / Kameleo / Octo / GoLogin).
 *
 * Uses a real antidetect browser with kernel-level fingerprint spoofing,
 * which is much harder to detect than JS-level overrides (Level 1).
 *
 * Supported tools (April 2026):
 *   - Camoufox: Firefox-based, open-source, Playwright-native API
 *     -> Best for self-hosted, no license cost, excellent undetectability
 *   - Kameleo: Commercial, local REST API, Chromium/Firefox/WebKit kernels
 *     -> Best fingerprint diversity, per-seat license
 *   - Octo Browser: Commercial, local API, Chromium-based
 *   - GoLogin: Commercial, API-based profiles
 *
 * Default: Camoufox (free, excellent in 2026 benchmarks, native Playwright).
 *
 * Key advantage over Level 1: fingerprints are spoofed at the browser engine
 * level, not via JS injection. Canvas, WebGL, AudioContext, fonts, etc. are
 * all modified in the rendering pipeline, making detection nearly impossible.
 *
 * Dependencies:
 *   - camoufox (npm): Camoufox launcher for Playwright
 *   - playwright: For Kameleo/Octo via CDP connection
 *   - undici: For Kameleo local REST API calls
 */

import { robustConfig } from '../config';
import { getStickyProxy, stickyProxyHash, invalidateStickySession } from '../stickyProxy';
import { attachCost } from '../costCalculator';
import { AnySolver } from '@/utils/anysolver';
import { config } from '@/config';
import { parseAllegroListing } from '@/utils/parser';
import type { ScopedLogger } from '@/utils/logger';
import type { FetchStrategy, RobustFetchResult, RobustMetadata, AntidetectTool } from '../types';
import { defaultMetadata } from '../types';

// ---------------------------------------------------------------------------
// Conditional imports: only load if tool is configured
// ---------------------------------------------------------------------------

let camoufoxLaunch: ((options: any) => Promise<any>) | null = null;
let playwrightChromium: any = null;

async function loadCamoufox(): Promise<void> {
    if (camoufoxLaunch) return;
    try {
        const mod = await import('camoufox');
        camoufoxLaunch = mod.launch ?? mod.default?.launch;
    } catch {
        throw new Error('camoufox package not installed. Run: pnpm add camoufox');
    }
}

async function loadPlaywright(): Promise<void> {
    if (playwrightChromium) return;
    try {
        const pw = await import('playwright');
        playwrightChromium = pw.chromium;
    } catch {
        throw new Error('playwright package not installed. Run: pnpm add playwright');
    }
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ALLEGRO_LISTING_URL = 'https://allegro.pl/listing';
const MAX_RETRIES = 2;

const USER_AGENT =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36';

// ---------------------------------------------------------------------------
// Strategy class
// ---------------------------------------------------------------------------

export class AntidetectBrowserStrategy implements FetchStrategy {
    readonly name = 'antidetectBrowser' as const;
    readonly level = 2 as const;

    private logger: ScopedLogger;
    private anysolver: AnySolver;
    private tool: AntidetectTool;

    constructor(logger: ScopedLogger) {
        this.logger = logger.scoped('Antidetect');
        this.anysolver = new AnySolver(config.ANYSOLVER_API_KEY, this.logger.scoped('AnySolver'));
        this.tool = robustConfig.ANTIDETECT.default_tool;
    }

    async isAvailable(): Promise<boolean> {
        switch (this.tool) {
            case 'camoufox':
                try {
                    await loadCamoufox();
                    return true;
                } catch { return false; }
            case 'kameleo':
                return !!robustConfig.ANTIDETECT.kameleo_api_url;
            case 'octo':
            case 'gologin':
                // These require their own desktop apps running
                return false; // TODO: implement health check for these
            default:
                return false;
        }
    }

    async fetch(ean: string, sessionHint?: string): Promise<RobustFetchResult> {
        const start = performance.now();
        const meta: RobustMetadata = defaultMetadata('antidetectBrowser', 2);
        meta.antidetect_tool = this.tool;

        // Use sticky residential proxy
        const { proxyUrl, sessionId, proxyType } = getStickyProxy(ean, 'sticky', sessionHint);
        meta.proxy_type = proxyType;
        meta.session_id = sessionId;

        this.logger.log(`EAN ${ean} using ${this.tool} + sticky session=${sessionId}`);

        // Dispatch to the appropriate tool implementation
        switch (this.tool) {
            case 'camoufox':
                return this.fetchWithCamoufox(ean, proxyUrl, sessionId, meta, start, sessionHint);
            case 'kameleo':
                return this.fetchWithKameleo(ean, proxyUrl, sessionId, meta, start, sessionHint);
            default:
                throw new Error(`Antidetect tool "${this.tool}" is not yet implemented`);
        }
    }

    async destroy(): Promise<void> {
        // Cleanup is done per-fetch (browser closed after each attempt)
        // For Kameleo, we could clean up profiles here
    }

    // -----------------------------------------------------------------------
    // Camoufox implementation (primary - recommended for 2026)
    // -----------------------------------------------------------------------

    /**
     * Camoufox: Firefox fork with kernel-level fingerprint spoofing.
     * Uses Playwright API natively - no CDP bridge needed.
     *
     * Key features:
     *   - OS-level font enumeration spoofing
     *   - Canvas/WebGL rendered at engine level (not JS overrides)
     *   - navigator properties spoofed in C++ layer
     *   - Consistent fingerprint across all detection vectors
     */
    private async fetchWithCamoufox(
        ean: string,
        proxyUrl: string,
        sessionId: string,
        meta: RobustMetadata,
        start: number,
        sessionHint?: string,
    ): Promise<RobustFetchResult> {
        await loadCamoufox();
        if (!camoufoxLaunch) throw new Error('Camoufox not loaded');

        const proxy = new URL(proxyUrl);
        let browser: any = null;
        let lastError: Error | null = null;
        let captchaSolves = 0;
        let datadomeSolves = 0;

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            try {
                // Launch Camoufox with antidetect config
                browser = await camoufoxLaunch({
                    headless: robustConfig.STEALTH_PLAYWRIGHT.headless,
                    // Camoufox-specific: humanize fingerprint
                    humanize: true,
                    // Locale and timezone for Polish Allegro
                    locale: 'pl-PL',
                    geoip: true, // Auto-detect geo from proxy IP
                    proxy: {
                        server: `${proxy.protocol}//${proxy.hostname}:${proxy.port}`,
                        username: decodeURIComponent(proxy.username),
                        password: decodeURIComponent(proxy.password),
                    },
                    // Screen and window randomization
                    screen: {
                        maxWidth: robustConfig.STEALTH_PLAYWRIGHT.viewport_width,
                        maxHeight: robustConfig.STEALTH_PLAYWRIGHT.viewport_height,
                    },
                });

                const context = browser.contexts()[0] ?? await browser.newContext();
                const page = await context.newPage();

                // Navigate
                const url = `${ALLEGRO_LISTING_URL}?string=${ean}&order=p`;
                this.logger.log(`Camoufox: navigating to ${url} (attempt ${attempt + 1})`);

                await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 40_000 });

                // Handle CAPTCHA challenges
                const body = await page.content();
                const captchaResult = await this.handleCaptchas(page, body, url, proxyUrl, ean);
                captchaSolves += captchaResult.captchaSolves;
                datadomeSolves += captchaResult.datadomeSolves;

                // Human-like behavior
                await this.simulateHuman(page);

                // Extract and parse
                const html = await page.content();
                const parsed = parseAllegroListing(html, ean);
                const durationMs = Math.round(performance.now() - start);

                const result: RobustFetchResult = {
                    ...parsed,
                    html,
                    durationMs,
                    scrapedAt: new Date().toISOString(),
                    captchaSolves,
                    proxyAttempts: attempt + 1,
                    proxyUrlHash: stickyProxyHash(proxyUrl),
                    proxySuccess: true,
                    ...meta,
                    browser_runtime_ms: durationMs,
                };

                attachCost(result, {
                    captcha_solves: captchaSolves,
                    datadome_solves: datadomeSolves,
                    browser_runtime_ms: durationMs,
                    estimated_kb: null,
                });

                this.logger.activity(`EAN ${ean} OK via camoufox in ${durationMs}ms`, 'success');
                return result;
            } catch (err) {
                lastError = err instanceof Error ? err : new Error(String(err));
                this.logger.log(`Camoufox attempt ${attempt + 1} failed: ${lastError.message}`);

                if (lastError.message.includes('IP is banned') || lastError.message.includes('t=bv')) {
                    invalidateStickySession(ean, 'sticky', sessionHint);
                }
            } finally {
                if (browser) await browser.close().catch(() => {});
                browser = null;
            }
        }

        throw lastError ?? new Error('AntidetectBrowser (camoufox): all attempts failed');
    }

    // -----------------------------------------------------------------------
    // Kameleo implementation (commercial alternative)
    // -----------------------------------------------------------------------

    /**
     * Kameleo: Commercial antidetect with local REST API.
     *
     * Flow:
     *   1. POST /profiles -> create profile with proxy + fingerprint config
     *   2. POST /profiles/{id}/start -> start browser, get CDP WebSocket URL
     *   3. Connect Playwright via CDP
     *   4. Do the scraping
     *   5. POST /profiles/{id}/stop -> close
     */
    private async fetchWithKameleo(
        ean: string,
        proxyUrl: string,
        sessionId: string,
        meta: RobustMetadata,
        start: number,
        sessionHint?: string,
    ): Promise<RobustFetchResult> {
        await loadPlaywright();
        const kameleoUrl = robustConfig.ANTIDETECT.kameleo_api_url;
        const proxy = new URL(proxyUrl);

        let profileId: string | null = null;
        let browser: any = null;
        let lastError: Error | null = null;
        let captchaSolves = 0;
        let datadomeSolves = 0;

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            try {
                // 1. Create Kameleo profile
                const { Pool } = await import('undici');
                const apiClient = new Pool(kameleoUrl);

                const createRes = await apiClient.request({
                    method: 'POST',
                    path: '/api/v2/profiles',
                    headers: {
                        'content-type': 'application/json',
                        ...(robustConfig.ANTIDETECT.kameleo_api_password
                            ? { authorization: `Bearer ${robustConfig.ANTIDETECT.kameleo_api_password}` }
                            : {}),
                    },
                    body: JSON.stringify({
                        name: `jj-${ean}-${sessionId}`,
                        os: 'windows',
                        browser: 'chromium',
                        language: 'pl-PL',
                        proxy: {
                            type: proxy.protocol.replace(':', '').toUpperCase(),
                            host: proxy.hostname,
                            port: Number(proxy.port),
                            username: decodeURIComponent(proxy.username),
                            password: decodeURIComponent(proxy.password),
                        },
                    }),
                });
                const profile = (await createRes.body.json()) as any;
                profileId = profile.id;

                // 2. Start browser and get CDP URL
                const startRes = await apiClient.request({
                    method: 'POST',
                    path: `/api/v2/profiles/${profileId}/start`,
                    headers: { 'content-type': 'application/json' },
                    body: JSON.stringify({}),
                });
                const startData = (await startRes.body.json()) as any;
                const cdpUrl = startData.externalSpoofEnginePort
                    ? `http://localhost:${startData.externalSpoofEnginePort}`
                    : startData.cdpUrl;

                // 3. Connect Playwright via CDP
                browser = await playwrightChromium.connectOverCDP(cdpUrl);
                const context = browser.contexts()[0];
                const page = context?.pages()[0] ?? await context.newPage();

                // 4. Navigate and scrape (same logic as Camoufox)
                const url = `${ALLEGRO_LISTING_URL}?string=${ean}&order=p`;
                this.logger.log(`Kameleo: navigating to ${url} (attempt ${attempt + 1})`);

                await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 40_000 });

                const body = await page.content();
                const captchaResult = await this.handleCaptchas(page, body, url, proxyUrl, ean);
                captchaSolves += captchaResult.captchaSolves;
                datadomeSolves += captchaResult.datadomeSolves;

                await this.simulateHuman(page);

                const html = await page.content();
                const parsed = parseAllegroListing(html, ean);
                const durationMs = Math.round(performance.now() - start);

                const result: RobustFetchResult = {
                    ...parsed,
                    html,
                    durationMs,
                    scrapedAt: new Date().toISOString(),
                    captchaSolves,
                    proxyAttempts: attempt + 1,
                    proxyUrlHash: stickyProxyHash(proxyUrl),
                    proxySuccess: true,
                    ...meta,
                    browser_runtime_ms: durationMs,
                };

                attachCost(result, {
                    captcha_solves: captchaSolves,
                    datadome_solves: datadomeSolves,
                    browser_runtime_ms: durationMs,
                    estimated_kb: null,
                });

                this.logger.activity(`EAN ${ean} OK via kameleo in ${durationMs}ms`, 'success');
                return result;
            } catch (err) {
                lastError = err instanceof Error ? err : new Error(String(err));
                this.logger.log(`Kameleo attempt ${attempt + 1} failed: ${lastError.message}`);

                if (lastError.message.includes('IP is banned')) {
                    invalidateStickySession(ean, 'sticky', sessionHint);
                }
            } finally {
                // 5. Stop and clean up Kameleo profile
                if (browser) await browser.close().catch(() => {});
                if (profileId) {
                    try {
                        const { Pool } = await import('undici');
                        const apiClient = new Pool(kameleoUrl);
                        await apiClient.request({
                            method: 'POST',
                            path: `/api/v2/profiles/${profileId}/stop`,
                            headers: { 'content-type': 'application/json' },
                            body: JSON.stringify({}),
                        });
                    } catch { /* best effort cleanup */ }
                }
                browser = null;
                profileId = null;
            }
        }

        throw lastError ?? new Error('AntidetectBrowser (kameleo): all attempts failed');
    }

    // -----------------------------------------------------------------------
    // Shared helpers
    // -----------------------------------------------------------------------

    /**
     * Handle DataDome and Allegro rate limiter CAPTCHAs.
     * Shared between Camoufox and Kameleo paths.
     */
    private async handleCaptchas(
        page: any,
        body: string,
        pageUrl: string,
        proxyUrl: string,
        ean: string,
    ): Promise<{ captchaSolves: number; datadomeSolves: number }> {
        let captchaSolves = 0;
        let datadomeSolves = 0;

        // DataDome check
        if (body.includes('captcha-delivery.com')) {
            this.logger.log('DataDome detected in antidetect browser');
            captchaSolves++;
            datadomeSolves++;

            const proxy = new URL(proxyUrl);
            const ddMatch = body.match(/var\s+dd\s*=\s*(\{[^}]+\})/);
            if (!ddMatch) throw new Error('Could not extract dd config');
            const dd = JSON.parse(ddMatch[1].replace(/'/g, '"'));

            if (dd.t === 'bv') throw new Error('IP is banned by DataDome (t=bv)');

            const isCaptcha = dd.rt === 'c';
            const captchaUrl = new URL(`https://${dd.host}${isCaptcha ? '/captcha/' : '/interstitial/'}`);
            captchaUrl.searchParams.set('initialCid', dd.cid);
            captchaUrl.searchParams.set('hash', dd.hsh);
            captchaUrl.searchParams.set('cid', dd.cookie);
            captchaUrl.searchParams.set('referer', pageUrl);
            captchaUrl.searchParams.set('s', String(dd.s));
            captchaUrl.searchParams.set('dm', 'cd');

            const solution = await this.anysolver.solve({
                type: 'DataDomeSliderToken',
                websiteURL: pageUrl,
                captchaUrl: captchaUrl.toString(),
                userAgent: USER_AGENT,
                proxy: {
                    type: proxy.protocol.replace(':', ''),
                    host: proxy.hostname,
                    port: Number(proxy.port),
                    username: decodeURIComponent(proxy.username),
                    password: decodeURIComponent(proxy.password),
                },
            });

            // Set cookie in browser context
            const ddCookie = String(solution.datadome);
            const cookieParts = ddCookie.split(';')[0].split('=');
            await page.context().addCookies([{
                name: cookieParts[0] ?? 'datadome',
                value: cookieParts.slice(1).join('='),
                domain: '.allegro.pl',
                path: '/',
            }]);

            await this.humanDelay();
            await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
        }

        // Allegro rate limiter check
        const currentBody = await page.content();
        if (currentBody.includes('allegrocaptcha.com')) {
            this.logger.log('Allegro rate limiter detected in antidetect browser');
            captchaSolves++;

            const traceMatch = currentBody.match(
                /allegrocaptcha\.com\/captcha-frontend\/allegro\.pl\/([a-f0-9-]+)/,
            );
            if (!traceMatch) throw new Error('Could not extract trace ID');

            const proxy = new URL(proxyUrl);
            const solution = await this.anysolver.solve({
                type: 'ReCaptchaV2EnterpriseToken',
                websiteURL: `https://allegrocaptcha.com/captcha-frontend/allegro.pl/${traceMatch[1]}`,
                websiteKey: '6LcR_okUAAAAAPYrPe-HK_0RBER3Ombtm2Fw_0kj', // Allegro's known key
                proxy: {
                    type: proxy.protocol.replace(':', ''),
                    host: proxy.hostname,
                    port: Number(proxy.port),
                    username: decodeURIComponent(proxy.username),
                    password: decodeURIComponent(proxy.password),
                },
            });

            const token = String(solution.token ?? solution.gRecaptchaResponse ?? '');
            if (!token) throw new Error('No token in CAPTCHA solution');

            const responseToken = `recaptchaEnterprise-${token}`;
            await page.goto(
                `https://allegrocaptcha.com/captcha-frontend?responseToken=${encodeURIComponent(responseToken)}`,
                { waitUntil: 'domcontentloaded', timeout: 15_000 },
            );

            await this.humanDelay();
            await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
        }

        return { captchaSolves, datadomeSolves };
    }

    private async simulateHuman(page: any): Promise<void> {
        const steps = robustConfig.STEALTH_PLAYWRIGHT.scroll_steps;
        for (let i = 0; i < steps; i++) {
            const scrollY = 200 + Math.floor(Math.random() * 400);
            await page.evaluate((y: number) => window.scrollBy({ top: y, behavior: 'smooth' }), scrollY);
            const x = 100 + Math.floor(Math.random() * 1200);
            const y = 100 + Math.floor(Math.random() * 600);
            await page.mouse.move(x, y, { steps: 5 + Math.floor(Math.random() * 10) });
            await this.humanDelay(0.3);
        }
        await this.humanDelay(0.5);
    }

    private async humanDelay(multiplier = 1.0): Promise<void> {
        const cfg = robustConfig.STEALTH_PLAYWRIGHT;
        const min = cfg.human_delay_min_ms * multiplier;
        const max = cfg.human_delay_max_ms * multiplier;
        await new Promise((r) => setTimeout(r, min + Math.random() * (max - min)));
    }
}
