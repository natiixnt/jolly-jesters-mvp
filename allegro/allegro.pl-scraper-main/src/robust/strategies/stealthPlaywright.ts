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
import { attachCost } from '../costCalculator';
import { config } from '@/config';
import { getRandomProxy, proxyUrlHash } from '@/utils/proxy';
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
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

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
    private logger: ScopedLogger;
    private cfg = robustConfig.STEALTH_PLAYWRIGHT;

    constructor(logger: ScopedLogger) {
        this.logger = logger.scoped('StealthPW');
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

    async fetch(ean: string, _sessionHint?: string): Promise<RobustFetchResult> {
        const start = performance.now();
        const meta: RobustMetadata = defaultMetadata('stealthPlaywright', 1);
        meta.proxy_type = 'residential';

        let lastError: Error | null = null;

        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            // Pick a random proxy from the loaded pool (same as raw strategy)
            const proxy = getRandomProxy();
            const proxyStr = proxy.toString();
            this.logger.log(`EAN ${ean} attempt ${attempt + 1} via Playwright`);

            const context = await this.createContext(proxyStr);
            const page = await context.newPage();

            try {
                // Inject stealth patches before navigation
                if (this.cfg.canvas_noise) {
                    await this.injectCanvasNoise(page);
                }

                const url = `${ALLEGRO_LISTING_URL}?string=${ean}&order=p`;
                const response = await page.goto(url, {
                    waitUntil: 'domcontentloaded',
                    timeout: 40_000,
                });

                const body = await page.content();

                // If DataDome or rate limiter detected, try different proxy
                if (body.includes('captcha-delivery.com') || body.includes('allegrocaptcha.com')) {
                    this.logger.log(`CAPTCHA detected on attempt ${attempt + 1}, rotating proxy`);
                    throw new Error('CAPTCHA detected - rotating proxy');
                }

                // Check we got a real page (not error)
                if ((response?.status() ?? 0) !== 200) {
                    throw new Error(`Unexpected status: ${response?.status()}`);
                }

                // Human-like behavior
                await this.simulateHumanBehavior(page);

                // Extract and parse
                const html = await page.content();
                const parsed = parseAllegroListing(html, ean);
                const durationMs = Math.round(performance.now() - start);

                const result: RobustFetchResult = {
                    ...parsed,
                    html,
                    durationMs,
                    scrapedAt: new Date().toISOString(),
                    captchaSolves: 0,
                    proxyAttempts: attempt + 1,
                    proxyUrlHash: proxyUrlHash(proxy),
                    proxySuccess: true,
                    ...meta,
                    browser_runtime_ms: durationMs,
                };

                attachCost(result, {
                    captcha_solves: 0,
                    datadome_solves: 0,
                    browser_runtime_ms: durationMs,
                    estimated_kb: null,
                });

                this.logger.activity(`EAN ${ean} OK via stealthPlaywright in ${durationMs}ms`, 'success');
                return result;
            } catch (err) {
                lastError = err instanceof Error ? err : new Error(String(err));
                this.logger.log(`Playwright attempt ${attempt + 1} failed: ${lastError.message}`);
            } finally {
                await page.close().catch(() => {});
                await context.close().catch(() => {});
            }

            // Wait before retry with new proxy
            if (attempt < MAX_RETRIES) {
                await this.humanDelay(0.5);
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
            // Firefox has a completely different TLS fingerprint than Chromium
            // and is much harder for DataDome to detect in headless mode
            this.logger.log('Launching Firefox (stealth mode)...');
            const { firefox } = await loadPlaywright();
            this.browser = await firefox.launch({
                headless: true,
                firefoxUserPrefs: {
                    'general.useragent.override': USER_AGENT,
                },
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
    private async simulateHumanBehavior(page: any): Promise<void> {
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

}
