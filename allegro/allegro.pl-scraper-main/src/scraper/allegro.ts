import { config } from '@/config';
import { ModuleClient, SessionClient } from 'tlsclientwrapper';
import type { Cookie } from 'tlsclientwrapper';
import path from 'node:path';
import process from 'node:process';
import { AnySolver } from '@/utils/anysolver';
import setCookieParser from 'set-cookie-parser';
import { parseAllegroListing, type AllegroSearchResult } from '@/utils/parser';
import type { ScopedLogger } from '@/utils/logger';

export type AllegroFetchResult = AllegroSearchResult & {
    html: string;
    durationMs: number;
    scrapedAt: string;
    captchaSolves: number;
};

interface SkycaptchaTicketConfig {
    id: string;
    type: 'RECAPTCHA_ENTERPRISE' | 'SKYCAPTCHA' | 'EMERGENCY';
    config?: { siteKey: string; size: string; theme: string };
    challengeName: string;
    clientName: string;
}

interface DatadomeConfig {
    rt?: string;
    cid: string;
    hsh: string;
    t?: string;
    b?: string;
    s: string;
    e?: string;
    host: string;
    cookie: string;
}

type RequestResponse = Awaited<ReturnType<SessionClient['get']>>;

const moduleClient = new ModuleClient({
    customLibraryDownloadPath: path.join(process.cwd(), 'lib'),
});

const ANYSOLVER_API_KEY = config.ANYSOLVER_API_KEY;

const USER_AGENT =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36';

const MAX_DATADOME_RETRIES = 1;

export default class Allegro {
    private client: SessionClient;
    private proxy: URL;
    private logger: ScopedLogger;
    private anysolver: AnySolver;

    constructor(proxy: URL, logger: ScopedLogger) {
        this.proxy = proxy;
        this.logger = logger;
        this.anysolver = new AnySolver(ANYSOLVER_API_KEY, logger.scoped('AnySolver'));
        this.client = new SessionClient(moduleClient, {
            tlsClientIdentifier: 'chrome_133',
            retryIsEnabled: true,
            retryStatusCodes: [0],
            retryMaxCount: 2,
            withDebug: false,
            defaultHeaders: {
                'accept-encoding': 'gzip, deflate, br, zstd',
                'accept-language': 'pl-PL,pl;q=0.9',
                'user-agent': USER_AGENT,
            },
            withRandomTLSExtensionOrder: true,
            insecureSkipVerify: true,
            headerOrder: [
                'host',
                'user-agent',
                'accept',
                'accept-language',
                'accept-encoding',
                'content-type',
                'content-length',
                'origin',
                'connection',
                'referer',
                'cookie',
                'sec-fetch-dest',
                'sec-fetch-mode',
                'sec-fetch-site',
                'sec-fetch-user',
                'upgrade-insecure-requests',
                'priority',
            ],
            proxyUrl: this.proxy.toString(),
        });
    }

    async fetch(ean: string): Promise<AllegroFetchResult> {
        const start = performance.now();
        const pageUrl = `https://allegro.pl/listing?string=${ean}&order=p`;

        let res = await this.request(pageUrl);
        let cookies = this.extractCookies(res);
        let datadomeAttempts = 0;
        let captchaSolves = 0;

        while (res.status !== 200) {
            this.logger.log('Status', res.status);

            if (res.status === 403 && res.body.includes('captcha-delivery.com')) {
                if (datadomeAttempts >= MAX_DATADOME_RETRIES) {
                    throw new Error(`DataDome failed after ${MAX_DATADOME_RETRIES} attempts`);
                }
                datadomeAttempts++;
                captchaSolves++;
                this.logger.log(`DataDome attempt ${datadomeAttempts}/${MAX_DATADOME_RETRIES}`);

                const ddCookie = await this.solveDatadomeChallenge(res.body, pageUrl);
                cookies = this.mergeCookies(cookies, ddCookie);
                res = await this.request(pageUrl, cookies);
                continue;
            }

            if (res.status === 429 && res.body.includes('allegrocaptcha.com')) {
                this.logger.log('Rate limiter detected');
                captchaSolves++;
                const wdctxCookie = await this.solveRateLimiter(res.body);
                cookies = this.mergeCookies(cookies, wdctxCookie);
                res = await this.request(pageUrl, cookies);
                continue;
            }

            throw new Error(`Unexpected status: ${res.status}`);
        }

        this.logger.log('Page loaded');
        const scrapedAt = new Date().toISOString();
        return this.buildResult(res.body, ean, scrapedAt, start, captchaSolves);
    }

    private async request(url: string, cookies?: Cookie[]): Promise<RequestResponse> {
        const res = await this.client.get(url, {
            headers: {
                accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'sec-ch-ua': '"Google Chrome";v="133", "Chromium";v="133", "Not=A?Brand";v="24"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-user': '?1',
                'upgrade-insecure-requests': '1',
            },
            requestCookies: cookies,
            followRedirects: true,
        });

        if (res.status === 0 || res.body == null) {
            throw new Error(`Connection failed (status=${res.status}). Proxy may be dead or network issue.`);
        }

        return res;
    }

    private extractCookies(res: RequestResponse): Cookie[] {
        return Object.values(res.cookies).map((c) => ({
            name: c.name,
            value: c.value,
            domain: c.domain,
            path: c.path,
            expires: c.expires,
            maxAge: c.maxAge,
        }));
    }

    private mergeCookies(existing: Cookie[], newCookie: Cookie): Cookie[] {
        return [...existing.filter((c) => c.name !== newCookie.name), newCookie];
    }

    private buildResult(
        html: string,
        ean: string,
        scrapedAt: string,
        start: number,
        captchaSolves: number,
    ): AllegroFetchResult {
        const parsed = parseAllegroListing(html, ean);
        if (parsed.status === 'no_results') {
            const snippet = html.replace(/\s+/g, ' ').slice(0, 800);
            this.logger.activity(`no_results snippet: ${snippet}`, 'info');
        }
        const durationMs = Math.round(performance.now() - start);
        return { ...parsed, html, scrapedAt, durationMs, captchaSolves };
    }

    private async solveDatadomeChallenge(body: string, pageUrl: string): Promise<Cookie> {
        const dd = this.parseDatadomeConfig(body);
        const captchaUrl = this.buildDatadomeUrl(dd, pageUrl);

        this.logger.log('Solving DataDome:', dd.rt === 'c' ? 'captcha' : 'interstitial');
        const solution = await this.anysolver.solve({
            type: 'DataDomeSliderToken',
            websiteURL: pageUrl,
            captchaUrl,
            userAgent: USER_AGENT,
            proxy: {
                type: this.proxy.protocol.replace(':', ''),
                host: this.proxy.hostname,
                port: Number(this.proxy.port),
                username: this.proxy.username,
                password: this.proxy.password,
            },
        });

        return this.parseCookie(String(solution.datadome));
    }

    private parseDatadomeConfig(body: string): DatadomeConfig {
        const match = body.match(/var\s+dd\s*=\s*(\{[^}]+\})/);
        if (!match) throw new Error('Could not extract dd config');
        return JSON.parse(match[1].replace(/'/g, '"')) as DatadomeConfig;
    }

    private buildDatadomeUrl(dd: DatadomeConfig, pageUrl: string): string {
        if (dd.t === 'bv') {
            throw new Error('IP is banned by DataDome (t=bv). Change your proxy IP.');
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

    private async solveRateLimiter(body: string): Promise<Cookie> {
        const traceId = this.extractTraceId(body);
        this.logger.log('Trace ID:', traceId);

        const ticket = await this.fetchSkycaptchaTicket();
        const ticketConfig = this.parseTicketConfig(ticket);
        this.logger.log('Captcha type:', ticketConfig.type);

        if (ticketConfig.type !== 'RECAPTCHA_ENTERPRISE' || !ticketConfig.config?.siteKey) {
            throw new Error(`Unsupported captcha: ${ticketConfig.type}`);
        }

        const token = await this.solveRecaptchaEnterprise(ticketConfig.config.siteKey, traceId);
        return this.submitRateLimiterSolution(token, traceId);
    }

    private extractTraceId(body: string): string {
        const match = body.match(/allegrocaptcha\.com\/captcha-frontend\/allegro\.pl\/([a-f0-9-]+)/);
        if (!match) throw new Error('Could not extract trace ID');
        return match[1];
    }

    private async fetchSkycaptchaTicket(): Promise<string> {
        this.logger.log('Fetching ticket');
        const res = await this.client.post('https://edge.allegro.pl/captcha/tickets?clientName=rate-limiter', '', {
            headers: {
                'content-type': 'application/json',
                accept: 'application/vnd.allegro.public.v2+json',
                'accept-language': 'pl-PL',
            },
        });
        if (res.status !== 200) throw new Error(`Ticket fetch failed: ${res.status}`);
        return res.body;
    }

    private parseTicketConfig(token: string): SkycaptchaTicketConfig {
        const parts = token.split('.');
        if (parts.length !== 3) throw new Error('Invalid JWT');
        const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString()) as { tt: string };
        return JSON.parse(payload.tt) as SkycaptchaTicketConfig;
    }

    private async solveRecaptchaEnterprise(siteKey: string, traceId: string): Promise<string> {
        this.logger.log('Solving ReCaptcha Enterprise');
        const solution = await this.anysolver.solve({
            type: 'ReCaptchaV2EnterpriseTokenProxyLess',
            websiteURL: `https://allegrocaptcha.com/captcha-frontend/allegro.pl/${traceId}`,
            websiteKey: siteKey,
            pageTitle: 'Captcha',
            enterprisePayload: {},
        });
        const token = solution.token ?? solution.gRecaptchaResponse;
        if (!token) throw new Error('No token in solution');
        return String(token);
    }

    private async submitRateLimiterSolution(captchaToken: string, traceId: string): Promise<Cookie> {
        this.logger.log('Submitting solution');
        const responseToken = `recaptchaEnterprise-${captchaToken}`;
        const res = await this.client.post(
            `https://allegrocaptcha.com/captcha-frontend?responseToken=${encodeURIComponent(responseToken)}`,
            '',
            {
                headers: {
                    'content-type': 'application/json',
                    accept: 'application/json',
                    'x-domain': 'allegro.pl',
                    origin: 'https://allegrocaptcha.com',
                    referer: `https://allegrocaptcha.com/captcha-frontend/allegro.pl/${traceId}`,
                },
            },
        );
        if (res.status !== 200) throw new Error(`Submit failed: ${res.status}`);

        let wdctx = res.cookies['wdctx']?.value ?? res.headers['x-set-wdctx'] ?? res.headers['X-Set-Wdctx'];

        if (!wdctx) {
            this.logger.log('Headers:', JSON.stringify(res.headers));
            this.logger.log('Cookies:', JSON.stringify(res.cookies));
            throw new Error('No wdctx in response');
        }

        wdctx = String(wdctx).split(';')[0].trim();

        this.logger.log('Got wdctx cookie');
        return { name: 'wdctx', value: wdctx, domain: '.allegro.pl', path: '/', expires: 0, maxAge: 2592000 };
    }

    private parseCookie(raw: string): Cookie {
        const parsed = setCookieParser.parseString(raw);
        return {
            name: parsed.name,
            value: parsed.value,
            domain: parsed.domain ?? '.allegro.pl',
            path: parsed.path ?? '/',
            expires: parsed.expires ? Math.floor(parsed.expires.getTime() / 1000) : 0,
            maxAge: parsed.maxAge,
        };
    }

    public async destroySession(): Promise<void> {
        await this.client.destroySession();
    }
}
