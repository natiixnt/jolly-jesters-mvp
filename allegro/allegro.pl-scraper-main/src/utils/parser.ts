import { load, type CheerioAPI } from 'cheerio';

export type SearchStatus = 'exact_match' | 'similar_offers' | 'no_results';
export type OfferType = 'buy_now' | 'auction';
export type Condition = 'new' | 'used';
export type SellerType = 'super_seller' | 'company' | 'private';

type Article = ReturnType<CheerioAPI>;

export interface Price {
    amount: number;
    currency: string;
}

export interface UnitPrice {
    amount: number;
    unit: string;
}

export interface Rating {
    score: number;
    reviewCount: number;
}

export interface Seller {
    name: string;
    profileUrl: string;
    rating: number | null;
    type: SellerType;
    isSuperSeller: boolean;
}

export interface Delivery {
    isFree: boolean;
    isSmart: boolean;
    estimatedDate: string | null;
    isDelayed: boolean;
}

export interface AllegroProduct {
    name: string;
    link: string;
    offerId: string;
    imageUrl: string;
    price: Price;
    unitPrice: UnitPrice | null;
    priceWithDelivery: number | null;
    offerType: OfferType;
    timeLeft: string | null;
    condition: Condition | null;
    isPromoted: boolean;
    promotionBadges: string[];
    rating: Rating | null;
    recentSalesCount: number | null;
    seller: Seller;
    delivery: Delivery;
    hasAllegroPayLater: boolean;
    productInfoSheetUrl: string | null;
    productCardOffersCount: number | null;
    attributes: Record<string, string>;
}

export interface AllegroSearchResult {
    status: SearchStatus;
    ean: string;
    totalOfferCount: number | null;
    products: AllegroProduct[];
}

export function parseAllegroListing(html: string, ean: string): AllegroSearchResult {
    const $ = load(html);
    const hasSimilarOffersBanner = checkSimilarOffersBanner($);
    // Allegro frequently changes markup; try multiple selectors
    let articles = $('article');
    if (articles.length === 0) articles = $('div[data-role="offer"]');
    if (articles.length === 0) articles = $('[data-analytics-view-label="offer"]');
    if (articles.length === 0) articles = $('[data-box-name="items-v3"] article');

    if (articles.length === 0) {
        return {
            status: 'no_results',
            ean,
            totalOfferCount: 0,
            products: [],
        };
    }

    const status: SearchStatus = hasSimilarOffersBanner ? 'similar_offers' : 'exact_match';
    const products: AllegroProduct[] = [];

    articles.each((_, el) => {
        const article = $(el);
        const product = parseProduct($, article);
        if (product) {
            products.push(product);
        }
    });

    return {
        status,
        ean,
        totalOfferCount: parseTotalOfferCount($),
        products,
    };
}

function checkSimilarOffersBanner($: CheerioAPI): boolean {
    const banner = $('[data-test-tag="announcement"]');
    if (banner.length === 0) return false;
    const text = banner.text().toLowerCase();
    return text.includes('podobne oferty') || text.includes('nie mamy dokładnie');
}

function parseProduct($: CheerioAPI, article: Article): AllegroProduct | null {
    const name = parseName(article);
    const link = parseLink(article);
    if (!name || !link) return null;

    const price = parsePrice(article);
    if (!price) return null;

    return {
        name,
        link,
        offerId: parseOfferId(link),
        imageUrl: parseImage(article),
        price,
        unitPrice: parseUnitPrice(article),
        priceWithDelivery: parsePriceWithDelivery(article),
        offerType: parseOfferType(article),
        timeLeft: parseTimeLeft(article),
        condition: parseCondition($, article),
        isPromoted: checkIsPromoted(article),
        promotionBadges: parsePromotionBadges(article),
        rating: parseRating(article),
        recentSalesCount: parseSalesCount(article),
        seller: parseSeller($, article),
        delivery: parseDelivery(article),
        hasAllegroPayLater: checkAllegroPayLater(article),
        productInfoSheetUrl: parseProductInfoSheet(article),
        productCardOffersCount: parseProductCardOffersCount(article),
        attributes: parseAttributes($, article),
    };
}

function parseName(article: Article): string | null {
    const name =
        article.find('h2 a').first().text().trim() ||
        article.find('a[data-role="offer-title"]').first().text().trim() ||
        article.find('a[data-analytics-view-custom-index]').first().text().trim();
    return name || null;
}

function parseLink(article: Article): string | null {
    const href =
        article.find('h2 a').first().attr('href') ||
        article.find('a[data-role="offer-title"]').first().attr('href') ||
        article.find('a[data-analytics-view-custom-index]').first().attr('href');
    if (!href) return null;
    if (href.includes('/events/clicks?')) {
        const redirectMatch = href.match(/redirect=([^&]+)/);
        if (redirectMatch) {
            return decodeURIComponent(redirectMatch[1]);
        }
    }
    return href.startsWith('https://') ? href : `https://allegro.pl${href.startsWith('/') ? '' : '/'}${href}`;
}

function parseOfferId(link: string): string {
    const offerIdMatch = link.match(/[/-](\d{10,})(?:\?|$)/);
    if (offerIdMatch) return offerIdMatch[1];
    const queryMatch = link.match(/offerId=(\d+)/);
    if (queryMatch) return queryMatch[1];
    const segments = link.split('/').filter(Boolean);
    return segments[segments.length - 1] || '';
}

function parseImage(article: Article): string {
    const img = article.find('img[src*="allegroimg.com/s"]').first();
    return img.attr('src') ?? '';
}

function parsePrice(article: Article): Price | null {
    // Primary: aria-label on price paragraph
    let label = article.find('p[aria-label*="aktualna cena"]').first().attr('aria-label') ?? '';
    if (!label) {
        // Fallback: meta tags or data-price attribute
        const metaPrice = article.find('meta[itemprop="price"]').attr('content');
        const metaCurrency = article.find('meta[itemprop="priceCurrency"]').attr('content');
        if (metaPrice && metaCurrency) {
            const amount = parseFloat(metaPrice.replace(',', '.'));
            return { amount, currency: metaCurrency };
        }
        const dataPrice = article.attr('data-price') || article.find('[data-price]').attr('data-price');
        const dataCurrency = article.attr('data-currency') || article.find('[data-currency]').attr('data-currency');
        if (dataPrice && dataCurrency) {
            const amount = parseFloat(String(dataPrice).replace(',', '.'));
            return { amount, currency: String(dataCurrency) };
        }
        label = article.text();
    }

    const match = label.match(/([\d\s,.]+)\s*(zł|pln|eur|€|\w{3})/i);
    if (!match) return null;
    const amount = parseFloat(match[1].replace(/\s/g, '').replace(',', '.'));
    const currency = match[2].replace(/\u00a0/g, '').trim();
    if (Number.isNaN(amount)) return null;
    return { amount, currency };
}

function parseUnitPrice(article: Article): UnitPrice | null {
    const text = article.text();
    const match = text.match(/([\d,]+)\s*zł\/(szt|kg|l|m|g|ml)\.?/i);
    if (!match) return null;
    return {
        amount: parseFloat(match[1].replace(',', '.')),
        unit: match[2].toLowerCase(),
    };
}

function parsePriceWithDelivery(article: Article): number | null {
    const text = article.text();
    const match = text.match(/([\d,]+)\s*zł\s*z\s*dostaw/i);
    if (!match) return null;
    return parseFloat(match[1].replace(',', '.'));
}

function parseOfferType(article: Article): OfferType {
    const text = article.text().toUpperCase();
    if (text.includes('LICYTACJA')) return 'auction';
    return 'buy_now';
}

function parseTimeLeft(article: Article): string | null {
    const text = article.text();
    const match = text.match(/(\d+\s*(?:dni|godz|min|sek))/i);
    return match ? match[1] : null;
}

function parseCondition($: CheerioAPI, article: Article): Condition | null {
    const attrs = parseAttributes($, article);
    const stan = attrs['Stan']?.toLowerCase();
    if (stan) {
        if (stan.includes('nowy')) return 'new';
        if (stan.includes('używan')) return 'used';
    }
    const text = article.text().toLowerCase();
    if (text.includes('stan') && text.includes('nowy')) return 'new';
    if (text.includes('stan') && text.includes('używan')) return 'used';
    return null;
}

function checkIsPromoted(article: Article): boolean {
    const text = article.text().toLowerCase();
    return text.includes('promowane') || text.includes('sponsorowane') || text.includes('supercena');
}

function parsePromotionBadges(article: Article): string[] {
    const badges: string[] = [];
    const text = article.text();
    if (/promowane/i.test(text)) badges.push('Promowane');
    if (/sponsorowane/i.test(text)) badges.push('Sponsorowane');
    if (/supercena/i.test(text)) badges.push('SUPERCENA');
    if (/wyrób medyczny/i.test(text)) badges.push('Wyrób medyczny');
    if (/bestseller/i.test(text)) badges.push('Bestseller');
    if (/gwarancja najniższej ceny/i.test(text)) badges.push('Gwarancja najniższej ceny');
    return badges;
}

function parseRating(article: Article): Rating | null {
    const ratingEl = article.find('[aria-label*="na 5"]').first();
    const label = ratingEl.attr('aria-label') ?? '';
    const match = label.match(/([\d,]+)\s*na\s*5.*?(\d+)\s*ocen/);
    if (!match) return null;
    return {
        score: parseFloat(match[1].replace(',', '.')),
        reviewCount: parseInt(match[2], 10),
    };
}

function parseSalesCount(article: Article): number | null {
    const btn = article
        .find(
            'button[aria-label*="kupiła ostatnio"], button[aria-label*="kupił ostatnio"], button[aria-label*="kupiło ostatnio"]',
        )
        .first();
    const label = btn.attr('aria-label') ?? '';
    const match = label.match(/(\d+)\s*(osoba|osoby|osób)/);
    return match ? parseInt(match[1], 10) : null;
}

function parseSeller($: CheerioAPI, article: Article): Seller {
    let name = 'Unknown';
    let rating: number | null = null;
    let profileUrl = '';

    const sellerLink = article.find('a[href*="/uzytkownik/"]').first();
    const href = sellerLink.attr('href') ?? '';
    const sellerText = sellerLink.text().trim();

    if (sellerText) {
        const match = sellerText.match(/^(.+?)\s*-\s*([\d,]+)%?$/);
        if (match) {
            name = match[1].trim();
            rating = parseFloat(match[2].replace(',', '.'));
        } else {
            name = sellerText;
        }
    }

    if (href) {
        profileUrl = href.startsWith('https://') ? href : `https://allegro.pl${href}`;
        if (name === 'Unknown') {
            const usernameMatch = href.match(/\/uzytkownik\/([^/?]+)/);
            if (usernameMatch) {
                name = decodeURIComponent(usernameMatch[1]);
            }
        }
    }

    const articleText = article.text().toLowerCase();
    const isSuperSeller = articleText.includes('super sprzedaw');
    const isCompany = articleText.includes('firma');

    let type: SellerType = 'private';
    if (isSuperSeller) type = 'super_seller';
    else if (isCompany) type = 'company';

    return { name, profileUrl, rating, type, isSuperSeller };
}

function parseDelivery(article: Article): Delivery {
    const text = article.text().toLowerCase();
    const isFree = text.includes('darmowa dostawa');
    const isSmart = article.find('img[alt*="Smart"]').length > 0 || text.includes('smart!');

    let estimatedDate: string | null = null;
    let isDelayed = false;

    const deliveryButton = article.find('button[aria-label*="dostawa"]').first();
    const deliveryLabel = deliveryButton.attr('aria-label') ?? '';

    if (deliveryLabel) {
        const dateMatch = deliveryLabel.match(/dostawa\s+(we?\s+\w+|w\s+\w+|za\s+\d+\s*dni)/i);
        if (dateMatch) {
            estimatedDate = dateMatch[1].trim();
        }
    }

    if (!estimatedDate) {
        const deliverySpan = article
            .find('span:contains("dostawa")')
            .filter((_, el) => {
                const spanText = (el as unknown as { children?: { data?: string }[] }).children?.[0]?.data ?? '';
                return /dostawa\s+(we?|w|za)/i.test(spanText);
            })
            .first();

        if (deliverySpan.length > 0) {
            const spanText = deliverySpan.text();
            const dateMatch = spanText.match(/dostawa\s+(we?\s+\w+|w\s+\w+|za\s+\d+\s*dni)/i);
            if (dateMatch) {
                estimatedDate = dateMatch[1].trim();
            }
        }
    }

    if (!estimatedDate) {
        const patterns = [
            /dostawa\s+(we?\s+wtorek)/i,
            /dostawa\s+(we?\s+środę)/i,
            /dostawa\s+(we?\s+czwartek)/i,
            /dostawa\s+(we?\s+piątek)/i,
            /dostawa\s+(w\s+sobotę)/i,
            /dostawa\s+(w\s+niedzielę)/i,
            /dostawa\s+(w\s+poniedziałek)/i,
            /dostawa\s+(za\s+\d+\s*dni)/i,
        ];

        for (const pattern of patterns) {
            const match = text.match(pattern);
            if (match) {
                estimatedDate = match[1].trim();
                break;
            }
        }
    }

    if (estimatedDate) {
        const daysMatch = estimatedDate.match(/za\s+(\d+)\s*dni/i);
        if (daysMatch) {
            isDelayed = parseInt(daysMatch[1], 10) > 7;
        }
    }

    return { isFree, isSmart, estimatedDate, isDelayed };
}

function checkAllegroPayLater(article: Article): boolean {
    const text = article.text().toLowerCase();
    return text.includes('zapłać później') || text.includes('allegro pay');
}

function parseProductInfoSheet(article: Article): string | null {
    const link = article.find('a[data-analytics-interaction-label="informationSheet"]').first();
    return link.attr('href') ?? null;
}

function parseProductCardOffersCount(article: Article): number | null {
    const link = article.find('a[data-role-type="product-fiche-link"]').first();
    const text = link.text();
    const match = text.match(/zobacz\s+(\d+)\s+ofer/i);
    return match ? parseInt(match[1], 10) : null;
}

function parseAttributes($: CheerioAPI, article: Article): Record<string, string> {
    const attrs: Record<string, string> = {};
    article.find('dl dt').each((_, dtEl) => {
        const dt = $(dtEl);
        const key = dt.text().trim();
        const value = dt.next('dd').text().trim();
        if (key && value) attrs[key] = value;
    });
    return attrs;
}

function parseTotalOfferCount($: CheerioAPI): number | null {
    const countEl = $('span._24159_R3FS9').first();
    if (countEl.length > 0) {
        const count = parseInt(countEl.text().trim(), 10);
        if (!isNaN(count)) return count;
    }
    const text = $('body').text();
    const match = text.match(/(\d+)\s*(?:ofert|wynik)/i);
    return match ? parseInt(match[1], 10) : null;
}
