/**
 * Level 1 Fallback: Stealth Playwright with advanced anti-detection.
 *
 * Uses playwright-extra + stealth plugin with additional hardening:
 *   - Canvas/WebGL fingerprint noise injection
 *   - Ghost-cursor for realistic mouse movements
 *   - Human-like scrolling, random delays, natural timing
 *   - Sticky residential proxy per EAN (30-60 min sessions)
 *   - Persistent browser context (cookie jar survives across requests)
 *
 * This strategy is significantly slower than raw HTTP (~8-25s per EAN)
 * but much harder to detect than a plain TLS client.
 *
 * Dependencies:
 *   - playwright-extra + puppeteer-extra-plugin-stealth
 *   - ghost-cursor (createCursor)
 *   - playwright (chromium)
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
// Constants
// ---------------------------------------------------------------------------

// Lazy-load playwright to avoid bundler errors when it's not installed
let pw: typeof import('playwright') | null = null;
async function loadPlaywright(): Promise<typeof import('playwright')> {
    if (pw) return pw;
    try {
        pw = await import('playwright');
        return pw;
    } catch {
        throw new Error('playwright package not installed. Run: pnpm add playwright');
    }
}

const USER_AGENT =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36';

const ALLEGRO_LISTING_URL = 'https://allegro.pl/listing';

// Maximum retries within this strategy before giving up
const MAX_RETRIES = 2;

// ---------------------------------------------------------------------------
// Strategy class
// ---------------------------------------------------------------------------

export class StealthPlaywrightStrategy implements FetchStrategy {
    readonly name = 'stealthPlaywright' as const;
    readonly level = 1 as const;

    private browser: any = null;
    private anysolver: AnySolver;
    private logger: ScopedLogger;
    private cfg = robustConfig.STEALTH_PLAYWRIGHT;

    constructor(logger: ScopedLogger) {
        this.logger = logger.scoped('StealthPW');
        this.anysolver = new AnySolver(config.ANYSOLVER_API_KEY, this.logger.scoped('AnySolver'));
    }

    async isAvailable(): Promise<boolean> {
        try {
            const { chromium } = await loadPlaywright();
            const execPath = chromium.executablePath();
            return !!execPath;
        } catch {
            return false;
        }
    }

    async fetch(ean: string, sessionHint?: string): Promise<RobustFetchResult> {
        const start = performance.now();
        const meta: RobustMetadata = defaultMetadata('stealthPlaywright', 1);

        // Get sticky residential proxy for this EAN
        const { proxyUrl, sessionId, proxyType } = getStickyProxy(ean, 'sticky', sessionHint);
        meta.proxy_type = proxyType;
        meta.session_id = sessionId;

        this.logger.log(`EAN ${ean} using sticky proxy session=${sessionId}`);

        let lastError: Error | null = null;
        let captchaSolves = 0;
        let datadomeSolves = 0;

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            const context = await this.createContext(proxyUrl);
            const page = await context.newPage();

            try {
                // Inject canvas/WebGL noise before navigation
                if (this.cfg.canvas_noise) {
                    await this.injectCanvasNoise(page);
                }

                // Navigate to Allegro listing
                const url = `${ALLEGRO_LISTING_URL}?string=${ean}&order=p`;
                this.logger.log(`Navigating to ${url} (attempt ${attempt + 1})`);

                const response = await page.goto(url, {
                    waitUntil: 'domcontentloaded',
                    timeout: 30_000,
                });

                const status = response?.status() ?? 0;
                const body = await page.content();

                // Check for DataDome
                if (body.includes('captcha-delivery.com')) {
                    this.logger.log('DataDome detected, solving with proxy...');
                    captchaSolves++;
                    datadomeSolves++;

                    const proxyParsed = new URL(proxyUrl);
                    const solution = await this.anysolver.solve({
                        type: 'DataDomeSliderToken',
                        websiteURL: url,
                        captchaUrl: this.extractDatadomeCaptchaUrl(body, url),
                        userAgent: USER_AGENT,
                        proxy: {
                            type: proxyParsed.protocol.replace(':', ''),
                            host: proxyParsed.hostname,
                            port: Number(proxyParsed.port),
                            username: decodeURIComponent(proxyParsed.username),
                            password: decodeURIComponent(proxyParsed.password),
                        },
                    });

                    // Set the DataDome cookie and reload
                    const ddCookie = String(solution.datadome);
                    const cookieParts = ddCookie.split(';')[0].split('=');
                    await context.addCookies([{
                        name: cookieParts[0] ?? 'datadome',
                        value: cookieParts.slice(1).join('='),
                        domain: '.allegro.pl',
                        path: '/',
                    }]);

                    // Simulate human delay before retry
                    await this.humanDelay();
                    const retryResponse = await page.goto(url, {
                        waitUntil: 'domcontentloaded',
                        timeout: 30_000,
                    });

                    if (retryResponse?.status() !== 200) {
                        throw new Error(`DataDome failed after solve, status=${retryResponse?.status()}`);
                    }
                }

                // Check for Allegro rate limiter
                const finalBody = await page.content();
                if (finalBody.includes('allegrocaptcha.com')) {
                    this.logger.log('Allegro rate limiter detected, solving ReCAPTCHA...');
                    captchaSolves++;

                    // Extract trace ID and solve
                    const traceMatch = finalBody.match(
                        /allegrocaptcha\.com\/captcha-frontend\/allegro\.pl\/([a-f0-9-]+)/,
                    );
                    if (!traceMatch) throw new Error('Could not extract trace ID from rate limiter');
                    const traceId = traceMatch[1];

                    // Use page to interact with the captcha (more natural)
                    const siteKey = await this.extractSiteKeyFromPage(page, traceId);
                    const proxyParsed = new URL(proxyUrl);
                    const solution = await this.anysolver.solve({
                        type: 'ReCaptchaV2EnterpriseToken',
                        websiteURL: `https://allegrocaptcha.com/captcha-frontend/allegro.pl/${traceId}`,
                        websiteKey: siteKey,
                        proxy: {
                            type: proxyParsed.protocol.replace(':', ''),
                            host: proxyParsed.hostname,
                            port: Number(proxyParsed.port),
                            username: decodeURIComponent(proxyParsed.username),
                            password: decodeURIComponent(proxyParsed.password),
                        },
                    });

                    const token = String(solution.token ?? solution.gRecaptchaResponse ?? '');
                    if (!token) throw new Error('No token in CAPTCHA solution');

                    // Submit the solution via page navigation
                    const responseToken = `recaptchaEnterprise-${token}`;
                    await page.goto(
                        `https://allegrocaptcha.com/captcha-frontend?responseToken=${encodeURIComponent(responseToken)}`,
                        { waitUntil: 'domcontentloaded', timeout: 15_000 },
                    );

                    await this.humanDelay();
                    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
                }

                // Human-like behavior: scroll and wait
                await this.simulateHumanBehavior(page);

                // Extract final HTML and parse
                const html = await page.content();
                const parsed = parseAllegroListing(html, ean);
                const durationMs = Math.round(performance.now() - start);

                // Build result
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

                // Calculate cost
                attachCost(result, {
                    captcha_solves: captchaSolves,
                    datadome_solves: datadomeSolves,
                    browser_runtime_ms: durationMs,
                    estimated_kb: null,
                });

                this.logger.activity(`EAN ${ean} OK via stealthPlaywright in ${durationMs}ms`, 'success');
                return result;
            } catch (err) {
                lastError = err instanceof Error ? err : new Error(String(err));
                this.logger.log(`Attempt ${attempt + 1} failed: ${lastError.message}`);

                // If IP banned, invalidate the sticky session
                if (lastError.message.includes('IP is banned') || lastError.message.includes('t=bv')) {
                    invalidateStickySession(ean, 'sticky', sessionHint);
                }
            } finally {
                await page.close().catch(() => {});
                await context.close().catch(() => {});
            }
        }

        throw lastError ?? new Error('StealthPlaywright: all attempts failed');
    }

    async destroy(): Promise<void> {
        if (this.browser) {
            await this.browser.close().catch(() => {});
            this.browser = null;
        }
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    private async createContext(proxyUrl: string): Promise<any> {
        const proxy = new URL(proxyUrl);
        const browser = await this.ensureBrowser();

        return browser.newContext({
            proxy: {
                server: `${proxy.protocol}//${proxy.hostname}:${proxy.port}`,
                username: decodeURIComponent(proxy.username),
                password: decodeURIComponent(proxy.password),
            },
            userAgent: USER_AGENT,
            viewport: {
                width: this.cfg.viewport_width,
                height: this.cfg.viewport_height,
            },
            locale: 'pl-PL',
            timezoneId: 'Europe/Warsaw',
            geolocation: { latitude: 52.2297, longitude: 21.0122 }, // Warsaw
            permissions: [],
            // Realistic browser settings
            javaScriptEnabled: true,
            hasTouch: false,
            isMobile: false,
            colorScheme: 'light',
        });
    }

    private async ensureBrowser(): Promise<any> {
        if (!this.browser || !this.browser.isConnected()) {
            this.logger.log('Launching Chromium (stealth mode)...');
            const { chromium } = await loadPlaywright();
            this.browser = await chromium.launch({
                headless: this.cfg.headless,
                args: [
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-web-security',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--disable-extensions',
                    '--disable-dev-shm-usage',
                    // Realistic window size
                    `--window-size=${this.cfg.viewport_width},${this.cfg.viewport_height}`,
                ],
            });
        }
        return this.browser;
    }

    /**
     * Inject canvas/WebGL fingerprint noise into the page.
     * Adds subtle random offsets to canvas operations to break fingerprinting.
     */
    private async injectCanvasNoise(page: any): Promise<void> {
        await page.addInitScript(() => {
            // Randomize canvas toDataURL output
            const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function (type?: string, quality?: number) {
                const ctx = this.getContext('2d');
                if (ctx) {
                    const noise = Math.random() * 0.01;
                    const imageData = ctx.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {
                        // Add tiny noise to red channel only (invisible but unique)
                        imageData.data[i] = Math.min(255, imageData.data[i] + Math.floor(noise * 10));
                    }
                    ctx.putImageData(imageData, 0, 0);
                }
                return origToDataURL.call(this, type, quality);
            };

            // Override navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });

            // Override chrome.runtime to appear as real Chrome
            (window as any).chrome = {
                runtime: {
                    connect: () => {},
                    sendMessage: () => {},
                },
            };

            // Override permissions API
            const origQuery = Permissions.prototype.query;
            Permissions.prototype.query = function (desc: PermissionDescriptor) {
                if (desc.name === 'notifications') {
                    return Promise.resolve({ state: 'denied', onchange: null } as PermissionStatus);
                }
                return origQuery.call(this, desc);
            };

            // Override plugins to appear non-empty
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ],
            });

            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['pl-PL', 'pl', 'en-US', 'en'],
            });
        });
    }

    /**
     * Simulate human-like page interaction.
     * Scrolls, moves mouse, waits random durations.
     */
    private async simulateHumanBehavior(page: Page): Promise<void> {
        const steps = this.cfg.scroll_steps;

        for (let i = 0; i < steps; i++) {
            // Random scroll amount (200-600px)
            const scrollY = 200 + Math.floor(Math.random() * 400);
            await page.evaluate((y) => window.scrollBy({ top: y, behavior: 'smooth' }), scrollY);

            // Random mouse movement if ghost cursor enabled
            if (this.cfg.ghost_cursor) {
                const x = 100 + Math.floor(Math.random() * 1200);
                const y = 100 + Math.floor(Math.random() * 600);
                await page.mouse.move(x, y, { steps: 5 + Math.floor(Math.random() * 10) });
            }

            // Random delay between actions
            await this.humanDelay(0.3);
        }

        // Final wait before extracting content
        await this.humanDelay(0.5);
    }

    /**
     * Wait a random human-like duration.
     * @param multiplier - Scale factor (1.0 = use config range, 0.5 = half)
     */
    private async humanDelay(multiplier = 1.0): Promise<void> {
        const min = this.cfg.human_delay_min_ms * multiplier;
        const max = this.cfg.human_delay_max_ms * multiplier;
        const delay = min + Math.random() * (max - min);
        await new Promise((r) => setTimeout(r, delay));
    }

    /**
     * Extract the DataDome captcha URL from the response body.
     */
    private extractDatadomeCaptchaUrl(body: string, pageUrl: string): string {
        const match = body.match(/var\s+dd\s*=\s*(\{[^}]+\})/);
        if (!match) throw new Error('Could not extract dd config');
        const dd = JSON.parse(match[1].replace(/'/g, '"'));

        if (dd.t === 'bv') {
            throw new Error('IP is banned by DataDome (t=bv)');
        }

        const isCaptcha = dd.rt === 'c';
        const url = new URL(`https://${dd.host}${isCaptcha ? '/captcha/' : '/interstitial/'}`);
        url.searchParams.set('initialCid', dd.cid);
        url.searchParams.set('hash', dd.hsh);
        url.searchParams.set('cid', dd.cookie);
        url.searchParams.set('referer', pageUrl);
        url.searchParams.set('s', String(dd.s));
        url.searchParams.set('dm', 'cd');
        if (dd.e) url.searchParams.set('e', dd.e);
        if (isCaptcha && dd.t) url.searchParams.set('t', dd.t);
        if (!isCaptcha && dd.b) url.searchParams.set('b', String(dd.b));

        return url.toString();
    }

    /**
     * Extract ReCAPTCHA site key by fetching the ticket from Allegro's edge API.
     */
    private async extractSiteKeyFromPage(page: Page, _traceId: string): Promise<string> {
        // Fetch the ticket via the page's context (uses same cookies/proxy)
        const ticketResponse = await page.evaluate(async () => {
            const res = await fetch('https://edge.allegro.pl/captcha/tickets?clientName=rate-limiter', {
                method: 'POST',
                headers: {
                    'content-type': 'application/json',
                    accept: 'application/vnd.allegro.public.v2+json',
                    'accept-language': 'pl-PL',
                },
                body: '',
            });
            return res.text();
        });

        // Parse JWT to extract siteKey
        const parts = ticketResponse.split('.');
        if (parts.length !== 3) throw new Error('Invalid ticket JWT');
        const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
        const tt = JSON.parse(payload.tt);

        if (tt.type !== 'RECAPTCHA_ENTERPRISE' || !tt.config?.siteKey) {
            throw new Error(`Unsupported captcha type: ${tt.type}`);
        }

        return tt.config.siteKey;
    }
}
