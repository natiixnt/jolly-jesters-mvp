/**
 * Level 3 Fallback: Mobile Proxy + Maximum Human-Like Simulation.
 *
 * Last resort strategy. Uses:
 *   - Mobile 4G/LTE proxy (highest trust score, shared CGNAT IP pool)
 *   - Camoufox with mobile user agent and viewport
 *   - Extra-long human delays (3-8 seconds between actions)
 *   - Extended scroll simulation with random pauses
 *   - Mouse movement with Bezier curves (ghost-cursor style)
 *   - Random "distraction" actions (hover over elements, partial scroll back)
 *
 * This is the slowest strategy (~15-45s per EAN) but has the highest success
 * rate because mobile IPs are almost never blocked by DataDome (they are
 * shared among millions of real users via CGNAT).
 *
 * Dependencies: Same as Level 2 (camoufox/playwright)
 */

import { robustConfig } from '../config';
import { getStickyProxy, stickyProxyHash, invalidateStickySession } from '../stickyProxy';
import { attachCost } from '../costCalculator';
import { AnySolver } from '@/utils/anysolver';
import { config } from '@/config';
import { parseAllegroListing } from '@/utils/parser';
import type { ScopedLogger } from '@/utils/logger';
import type { FetchStrategy, RobustFetchResult, RobustMetadata } from '../types';
import { defaultMetadata } from '../types';

// ---------------------------------------------------------------------------
// Conditional import
// ---------------------------------------------------------------------------

let camoufoxLaunch: ((options: any) => Promise<any>) | null = null;

async function loadCamoufox(): Promise<void> {
    if (camoufoxLaunch) return;
    try {
        const mod = await import('camoufox');
        camoufoxLaunch = mod.launch ?? mod.default?.launch;
    } catch {
        throw new Error('camoufox package not installed');
    }
}

// ---------------------------------------------------------------------------
// Constants - extra conservative timings for mobile
// ---------------------------------------------------------------------------

const ALLEGRO_LISTING_URL = 'https://allegro.pl/listing';
const MAX_RETRIES = 3; // More retries since mobile proxy is expensive

// Mobile Chrome user agent (Samsung Galaxy S24 Ultra)
const MOBILE_USER_AGENT =
    'Mozilla/5.0 (Linux; Android 15; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Mobile Safari/537.36';

// Mobile viewport
const MOBILE_VIEWPORT = { width: 412, height: 915 };

// ---------------------------------------------------------------------------
// Strategy class
// ---------------------------------------------------------------------------

export class MobileFallbackStrategy implements FetchStrategy {
    readonly name = 'mobileFallback' as const;
    readonly level = 3 as const;

    private logger: ScopedLogger;
    private anysolver: AnySolver;

    constructor(logger: ScopedLogger) {
        this.logger = logger.scoped('MobileFB');
        this.anysolver = new AnySolver(config.ANYSOLVER_API_KEY, this.logger.scoped('AnySolver'));
    }

    async isAvailable(): Promise<boolean> {
        // Requires mobile proxy endpoint + camoufox
        if (!robustConfig.STICKY_PROXY.mobile_endpoint) return false;
        try {
            await loadCamoufox();
            return true;
        } catch { return false; }
    }

    async fetch(ean: string, sessionHint?: string): Promise<RobustFetchResult> {
        await loadCamoufox();
        if (!camoufoxLaunch) throw new Error('Camoufox not loaded');

        const start = performance.now();
        const meta: RobustMetadata = defaultMetadata('mobileFallback', 3);
        meta.antidetect_tool = 'camoufox';

        // Get sticky MOBILE proxy
        const { proxyUrl, sessionId, proxyType } = getStickyProxy(ean, 'mobile', sessionHint);
        meta.proxy_type = proxyType;
        meta.session_id = sessionId;

        this.logger.log(`EAN ${ean} using MOBILE proxy session=${sessionId}`);

        let browser: any = null;
        let lastError: Error | null = null;
        let captchaSolves = 0;
        let datadomeSolves = 0;

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            try {
                const proxy = new URL(proxyUrl);

                // Launch Camoufox in mobile mode
                browser = await camoufoxLaunch({
                    headless: robustConfig.STEALTH_PLAYWRIGHT.headless,
                    humanize: true,
                    locale: 'pl-PL',
                    geoip: true,
                    proxy: {
                        server: `${proxy.protocol}//${proxy.hostname}:${proxy.port}`,
                        username: decodeURIComponent(proxy.username),
                        password: decodeURIComponent(proxy.password),
                    },
                    screen: {
                        maxWidth: MOBILE_VIEWPORT.width,
                        maxHeight: MOBILE_VIEWPORT.height,
                    },
                });

                const context = browser.contexts()[0] ?? await browser.newContext({
                    userAgent: MOBILE_USER_AGENT,
                    viewport: MOBILE_VIEWPORT,
                    isMobile: true,
                    hasTouch: true,
                    locale: 'pl-PL',
                });

                const page = await context.newPage();

                // Extra-long initial delay (simulating user opening app)
                await this.mobileDelay(2000, 5000);

                // Navigate
                const url = `${ALLEGRO_LISTING_URL}?string=${ean}&order=p`;
                this.logger.log(`Mobile: navigating to ${url} (attempt ${attempt + 1})`);

                await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60_000 });

                // Handle CAPTCHAs (rare on mobile IP but still possible)
                const body = await page.content();
                if (body.includes('captcha-delivery.com')) {
                    this.logger.log('DataDome on mobile (rare!)');
                    captchaSolves++;
                    datadomeSolves++;

                    const ddMatch = body.match(/var\s+dd\s*=\s*(\{[^}]+\})/);
                    if (!ddMatch) throw new Error('Could not extract dd config');
                    const dd = JSON.parse(ddMatch[1].replace(/'/g, '"'));
                    if (dd.t === 'bv') throw new Error('IP is banned by DataDome (t=bv)');

                    const isCaptcha = dd.rt === 'c';
                    const captchaUrl = new URL(`https://${dd.host}${isCaptcha ? '/captcha/' : '/interstitial/'}`);
                    captchaUrl.searchParams.set('initialCid', dd.cid);
                    captchaUrl.searchParams.set('hash', dd.hsh);
                    captchaUrl.searchParams.set('cid', dd.cookie);
                    captchaUrl.searchParams.set('referer', url);
                    captchaUrl.searchParams.set('s', String(dd.s));
                    captchaUrl.searchParams.set('dm', 'cd');

                    const solution = await this.anysolver.solve({
                        type: 'DataDomeSliderToken',
                        websiteURL: url,
                        captchaUrl: captchaUrl.toString(),
                        userAgent: MOBILE_USER_AGENT,
                        proxy: {
                            type: proxy.protocol.replace(':', ''),
                            host: proxy.hostname,
                            port: Number(proxy.port),
                            username: decodeURIComponent(proxy.username),
                            password: decodeURIComponent(proxy.password),
                        },
                    });

                    const ddCookie = String(solution.datadome);
                    const cookieParts = ddCookie.split(';')[0].split('=');
                    await context.addCookies([{
                        name: cookieParts[0] ?? 'datadome',
                        value: cookieParts.slice(1).join('='),
                        domain: '.allegro.pl',
                        path: '/',
                    }]);

                    await this.mobileDelay(3000, 6000);
                    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 40_000 });
                }

                // Extended mobile human simulation
                await this.simulateMobileUser(page);

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

                this.logger.activity(`EAN ${ean} OK via mobileFallback in ${durationMs}ms`, 'success');
                return result;
            } catch (err) {
                lastError = err instanceof Error ? err : new Error(String(err));
                this.logger.log(`Mobile attempt ${attempt + 1} failed: ${lastError.message}`);

                if (lastError.message.includes('IP is banned')) {
                    invalidateStickySession(ean, 'mobile', sessionHint);
                }

                // Longer delay between mobile retries
                if (attempt < MAX_RETRIES) {
                    await this.mobileDelay(5000, 10_000);
                }
            } finally {
                if (browser) await browser.close().catch(() => {});
                browser = null;
            }
        }

        throw lastError ?? new Error('MobileFallback: all attempts failed');
    }

    async destroy(): Promise<void> {
        // Per-fetch cleanup, nothing persistent
    }

    // -----------------------------------------------------------------------
    // Mobile-specific human simulation
    // -----------------------------------------------------------------------

    /**
     * Simulate a real mobile user browsing Allegro:
     *   - Touch-based scrolling (swipe gestures)
     *   - Longer pauses (mobile users read slower)
     *   - Occasional scroll-back (user reconsidering)
     *   - Tap on random elements (but don't navigate away)
     */
    private async simulateMobileUser(page: any): Promise<void> {
        // Initial read pause
        await this.mobileDelay(1500, 3000);

        // Scroll down in small touch-like increments
        const totalScrolls = 3 + Math.floor(Math.random() * 4); // 3-6 scrolls
        for (let i = 0; i < totalScrolls; i++) {
            const scrollY = 150 + Math.floor(Math.random() * 300);
            await page.evaluate((y: number) => window.scrollBy({ top: y, behavior: 'smooth' }), scrollY);
            await this.mobileDelay(800, 2500);

            // 20% chance of scrolling back slightly (reconsidering)
            if (Math.random() < 0.2) {
                const scrollBack = -(50 + Math.floor(Math.random() * 100));
                await page.evaluate((y: number) => window.scrollBy({ top: y, behavior: 'smooth' }), scrollBack);
                await this.mobileDelay(500, 1500);
            }

            // Touch-like tap at random position (no click, just move)
            const x = 50 + Math.floor(Math.random() * (MOBILE_VIEWPORT.width - 100));
            const y = 100 + Math.floor(Math.random() * (MOBILE_VIEWPORT.height - 200));
            await page.mouse.move(x, y, { steps: 3 });
        }

        // Final pause before extraction
        await this.mobileDelay(1000, 2000);
    }

    /**
     * Mobile-appropriate delay (longer than desktop).
     */
    private async mobileDelay(minMs: number, maxMs: number): Promise<void> {
        const delay = minMs + Math.random() * (maxMs - minMs);
        await new Promise((r) => setTimeout(r, delay));
    }
}
