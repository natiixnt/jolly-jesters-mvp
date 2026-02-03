"""Bright Data Web Unlocker provider for Allegro listings / PDP (request-based).

Lightweight HTML fetch (no browser) for:
- Listing: price + offer URL
- PDP: sold_count (when present server-side)

Used by the hybrid flow: Unlocker first, Selenium only as a fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import httpx
import redis

from app.core.config import settings
from app.services.schemas import AllegroResult
from app.utils.allegro_scraper_http import _extract_listing_state, _listing_elements_count
from app.utils.rate_limiter import rate_limiter, RateLimited

logger = logging.getLogger(__name__)


# ---------- Rate limiting & retry config ----------


def _scraper_mode() -> str:
    return (os.getenv("SCRAPER_MODE") or "internal").strip().lower()


def _bd_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("BD_TIMEOUT_S", "30")))
    except Exception:
        return 30.0


def _bd_max_retries() -> int:
    try:
        return max(1, int(os.getenv("BD_MAX_RETRIES", "3")))
    except Exception:
        return 3


def _bd_qps() -> float:
    try:
        return max(0.0, float(os.getenv("BD_QPS", "1.0")))
    except Exception:
        return 1.0


def _bd_max_pages() -> int:
    try:
        return max(1, int(os.getenv("BD_LISTING_MAX_PAGES", "3")))
    except Exception:
        return 3


def _bd_cache_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("BD_CACHE_TTL_SECONDS", str(24 * 3600))))
    except Exception:
        return 24 * 3600


def _pdp_tie_break_limit() -> int:
    try:
        return max(1, int(os.getenv("BD_PDP_TIE_BREAK_LIMIT", "5")))
    except Exception:
        return 5


_RATE_LOCK = threading.Lock()
_NEXT_ALLOWED_AT = 0.0


def _wait_for_rate_limit() -> None:
    qps = _bd_qps()
    if qps <= 0:
        return
    min_interval = 1.0 / qps
    global _NEXT_ALLOWED_AT
    with _RATE_LOCK:
        now = time.monotonic()
        wait_for = _NEXT_ALLOWED_AT - now
        if wait_for > 0:
            time.sleep(wait_for)
            now = time.monotonic()
        _NEXT_ALLOWED_AT = now + min_interval


# ---------- Redis cache ----------


_CACHE_CLIENT = None


def _redis_client():
    global _CACHE_CLIENT
    if _CACHE_CLIENT is None:
        try:
            _CACHE_CLIENT = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("BD_CACHE redis init failed err=%r", exc)
            _CACHE_CLIENT = None
    return _CACHE_CLIENT


def _cache_key(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"bd_unlocker:v1:{digest}"


def _read_cache(url: str) -> Optional[dict]:
    client = _redis_client()
    if not client:
        return None
    try:
        raw = client.get(_cache_key(url))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _write_cache(url: str, payload: dict) -> None:
    ttl = _bd_cache_ttl_seconds()
    if ttl <= 0:
        return
    client = _redis_client()
    if not client:
        return
    try:
        client.set(_cache_key(url), json.dumps(payload), ex=ttl)
    except Exception:
        return


# ---------- Helpers ----------


def _http_timeout() -> httpx.Timeout:
    total = _bd_timeout_seconds()
    connect = min(total, max(3.0, total / 3))
    read = min(total, max(5.0, total * 0.8))
    return httpx.Timeout(connect=connect, read=read, write=read, pool=connect)


def _resi_proxy_url() -> Optional[str]:
    host = os.getenv("BRD_RESI_HOST")
    port = os.getenv("BRD_RESI_PORT")
    user = os.getenv("BRD_RESI_USERNAME")
    pwd = os.getenv("BRD_RESI_PASSWORD")
    if not all([host, port, user, pwd]):
        return None
    return f"http://{user}:{pwd}@{host}:{port}"


def _resi_verify() -> bool:
    # Bright Data residential proxies often require their cert; allow opt-in verify
    return os.getenv("BRD_RESI_VERIFY_SSL", "0") not in ("0", "false", "False")


def _build_listing_url(ean: str, page: int = 1) -> str:
    base = "https://allegro.pl/listing"
    return f"{base}?string={ean}&order=qd&offerTypeBuyNow=1&p={page}"


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://allegro.pl{url}"
    return url


def _extract_price_currency(item: dict) -> Tuple[Optional[float], Optional[str]]:
    price_fields = [
        item.get("price"),
        item.get("price", {}).get("mainPrice"),
        item.get("price", {}).get("buyNow"),
        item.get("price", {}).get("withDiscount"),
        item.get("sellingMode", {}).get("price"),
    ]
    for candidate in price_fields:
        try:
            amount = candidate.get("amount")
            currency = candidate.get("currency") or candidate.get("currencyCode")
        except Exception:
            continue
        if amount is not None:
            try:
                return float(amount), currency
            except Exception:
                continue
    return None, None


def _parse_sold_label(label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    match = re.search(r"([0-9][0-9\s]*)", str(label))
    if not match:
        return None
    try:
        return int(match.group(1).replace(" ", ""))
    except Exception:
        return None


def _extract_offer_id(item: dict, url: Optional[str]) -> Optional[str]:
    for key in ("id", "offerId", "offerID"):
        if item.get(key):
            return str(item[key])
    try:
        pid = item.get("productDetails", {}).get("productId")
        if pid:
            return str(pid)
    except Exception:
        pass
    if url:
        match = re.search(r"offer[a]?/([0-9A-Za-z]+)", url)
        if match:
            return match.group(1)
    return None


@dataclass
class OfferCandidate:
    offer_id: Optional[str]
    url: Optional[str]
    price: Optional[float]
    currency: Optional[str]
    is_sponsored: Optional[bool]
    sold_count_hint: Optional[int]
    page: int
    raw: dict
    sold_count: Optional[int] = None
    sold_count_status: Optional[str] = None


def _parse_listing_offers(html: str, page: int) -> Tuple[List[OfferCandidate], bool]:
    state = _extract_listing_state(html) or {}
    elements = state.get("__listing_StoreState", {}).get("items", {}).get("elements", []) or []
    auctions_only = False
    offers: List[OfferCandidate] = []

    for el in elements:
        price, currency = _extract_price_currency(el)
        offer_url = _normalize_url(el.get("url") or el.get("productSeoLink", {}).get("url"))
        selling_mode = str(el.get("sellingMode")) if el.get("sellingMode") else ""
        is_auction = bool(el.get("isAuction") or selling_mode.lower().startswith("auction"))
        if is_auction:
            auctions_only = True
        if price is None or is_auction:
            continue

        offers.append(
            OfferCandidate(
                offer_id=_extract_offer_id(el, offer_url),
                url=offer_url,
                price=price,
                currency=currency,
                is_sponsored=bool(
                    el.get("promoted")
                    or el.get("promotionEmphasized")
                    or el.get("advertisement")
                    or el.get("sponsored")
                ),
                sold_count_hint=_parse_sold_label(el.get("productPopularity", {}).get("label")),
                page=page,
                raw=el,
            )
        )

    if not offers:
        elements_count = _listing_elements_count(state)
        if elements_count == 0:
            auctions_only = False
    return offers, auctions_only


_PDP_SOLD_PATTERNS = (
    r"sprzedan[oa]\s*([0-9][0-9\s]*)",
    r"\"soldCount\"\s*:\s*([0-9]+)",
    r"\"sold\"\s*:\s*([0-9]+)",
    r"\"unitsSold\"\s*:\s*([0-9]+)",
    r"\"quantitySold\"\s*:\s*([0-9]+)",
)


def _parse_pdp_sold_count(html: str) -> Optional[int]:
    lowered = html.lower()
    for pattern in _PDP_SOLD_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            try:
                return int(match.group(1).replace(" ", ""))
            except Exception:
                continue
    return None


async def _unlocker_fetch(url: str, label: str) -> Tuple[Optional[str], Dict[str, Any]]:
    cache_hit = False
    cache_payload = _read_cache(url)
    if cache_payload:
        cache_hit = True
        html = cache_payload.get("body")
        meta = cache_payload.get("meta") or {}
        meta.update({"cache": True})
        return html, meta

    token = os.getenv("BD_UNLOCKER_TOKEN") or settings.bd_unlocker_token
    endpoint = os.getenv("BD_UNLOCKER_ENDPOINT", "https://api.brightdata.com/unblocker")
    zone = os.getenv("BD_UNLOCKER_ZONE") or settings.bd_unlocker_zone
    if not token:
        return None, {"error": "bd_token_missing"}

    _wait_for_rate_limit()
    attempts = _bd_max_retries()
    timeout = _http_timeout()
    request_started = time.monotonic()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"url": url}
    if zone:
        payload["zone"] = zone

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=payload)
            duration = time.monotonic() - request_started
            meta = {
                "status_code": resp.status_code,
                "request_duration_seconds": round(duration, 2),
                "attempt": attempt,
                "endpoint": endpoint,
                "label": label,
            }
            if resp.status_code >= 500:
                raise httpx.HTTPError(f"server_error:{resp.status_code}")
            data = resp.json()
            solution = data.get("solution") or {}
            html = (
                solution.get("response")
                or solution.get("body")
                or solution.get("content")
                or solution.get("html")
            )
            meta["request_id"] = data.get("requestId") or data.get("request_id") or solution.get("id")
            if html is None:
                meta["error"] = "empty_body"
                return None, meta
            meta["cache"] = cache_hit
            _write_cache(url, {"body": html, "meta": meta})
            return html, meta
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            if attempt >= attempts:
                return None, {"error": "timeout", "attempt": attempt, "label": label}
            time.sleep(min(3.0, 0.5 * attempt))
        except Exception as exc:  # pragma: no cover - network failure fallback
            if attempt >= attempts:
                return None, {"error": str(exc), "label": label, "attempt": attempt}
            time.sleep(min(2.0, 0.5 * attempt))
    return None, {"error": "unknown"}


def _select_candidates(offers: List[OfferCandidate]) -> List[OfferCandidate]:
    priced = [o for o in offers if o.price is not None]
    if not priced:
        return []
    min_price = min(o.price for o in priced)
    return sorted([o for o in priced if o.price == min_price], key=lambda o: (o.offer_id or ""))[: _pdp_tie_break_limit()]


def _choose_best_offer(candidates: List[OfferCandidate]) -> Optional[OfferCandidate]:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda o: (-(o.sold_count if o.sold_count is not None else -1), o.offer_id or ""),
    )[0]


async def fetch_via_bd_unlocker(ean: str) -> AllegroResult:
    if _scraper_mode() != "bd_unlocker":
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "bd_override_disabled", "source": "bd_unlocker"},
            error="bd_override_disabled",
            source="cloud_http",
        )

    now = datetime.now(timezone.utc)
    listing_requests = 0
    pdp_requests = 0
    cache_hits = 0
    pages_scraped = 0
    auctions_only = False

    offers: List[OfferCandidate] = []
    listing_meta: List[dict] = []
    for page in range(1, _bd_max_pages() + 1):
        listing_url = _build_listing_url(ean, page=page)
        html, meta = await _unlocker_fetch(listing_url, label="listing")
        listing_meta.append(meta or {})
        if html:
            pages_scraped += 1
            listing_requests += 1
            cache_hits += 1 if meta.get("cache") else 0
            page_offers, auctions = _parse_listing_offers(html, page)
            auctions_only = auctions_only or auctions
            if page_offers:
                offers.extend(page_offers)
                break
        else:
            # Keep looping to next page; if all fail we'll return an error
            continue

    if not offers:
        sold_status = "auctions_only" if auctions_only else "no_offers_found"
        payload = {
            "provider": "bd_unlocker",
            "sold_count_status": sold_status,
            "listing_meta": listing_meta,
            "pages_scraped": pages_scraped,
            "listing_requests": listing_requests,
            "pdp_requests": pdp_requests,
            "cache_hits": cache_hits,
        }
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="cloud_http",
            last_checked_at=now,
        )

    candidates = _select_candidates(offers)
    sold_status = "not_visible"
    for candidate in candidates:
        pdp_url = _normalize_url(candidate.url)
        if not pdp_url:
            candidate.sold_count_status = "error"
            continue
        html, meta = await _unlocker_fetch(pdp_url, label="pdp")
        pdp_requests += 1
        cache_hits += 1 if meta.get("cache") else 0
        if not html:
            candidate.sold_count_status = "error"
            continue
        sold_count = _parse_pdp_sold_count(html)
        candidate.sold_count = sold_count
        candidate.sold_count_status = "ok" if sold_count is not None else "not_visible"

    best = _choose_best_offer(candidates)
    if best and best.sold_count_status == "ok":
        sold_status = "ok"
    elif best:
        sold_status = best.sold_count_status or "not_visible"

    selected_price = best.price if best else None
    selected_sold = best.sold_count if best else None
    selected_offer_id = best.offer_id if best else None
    selected_offer_url = best.url if best else None
    selected_is_sponsored = best.is_sponsored if best else None

    payload = {
        "provider": "bd_unlocker",
        "scraped_at": now.isoformat(),
        "listing_requests": listing_requests,
        "pdp_requests": pdp_requests,
        "cache_hits": cache_hits,
        "pages_scraped": pages_scraped,
        "listing_meta": listing_meta,
        "offers": [
            {
                "offer_id": o.offer_id,
                "url": o.url,
                "price": o.price,
                "currency": o.currency,
                "is_sponsored": o.is_sponsored,
                "sold_count_hint": o.sold_count_hint,
                "sold_count": o.sold_count,
                "sold_count_status": o.sold_count_status,
                "page": o.page,
            }
            for o in offers
        ],
        "lowest_price": selected_price,
        "lowest_price_offer_id": selected_offer_id,
        "lowest_price_offer_url": selected_offer_url,
        "sold_count_A": selected_sold,
        "sold_count_status": sold_status,
        "is_sponsored": selected_is_sponsored,
    }

    return AllegroResult(
        price=Decimal(str(selected_price)) if selected_price is not None else None,
        sold_count=selected_sold,
        is_not_found=selected_price is None,
        is_temporary_error=False,
        raw_payload=payload,
        source="cloud_http",
        last_checked_at=now,
        product_url=selected_offer_url,
        offers=payload.get("offers"),
    )


# ---------- Lightweight helpers for hybrid flow (listing + pdp via unlocker) ----------


@dataclass
class OfferCandidate:
    price: Optional[Decimal]
    currency: Optional[str]
    url: Optional[str]
    offer_id: Optional[str]
    seller: Optional[str] = None


@dataclass
class SoldCountResult:
    sold_count: Optional[int]
    status: str  # visible | missing | blocked | error
    reason: Optional[str] = None


async def fetch_listing_html_via_unlocker(ean: str) -> str:
    url = _build_listing_url(ean)
    cached = _read_cache(url)
    if cached and cached.get("body"):
        return cached["body"]
    _wait_for_rate_limit()
    async with httpx.AsyncClient(timeout=_http_timeout(), proxy=_resi_proxy_url(), verify=_resi_verify()) as client:
        try:
            async with rate_limiter.throttle(url):
                resp = await client.get(url)
        except RateLimited:
            raise
        except Exception as exc:
            logger.warning("BD_UNLOCKER listing fetch failed url=%s err=%r", url, exc, exc_info=True)
            raise
    if resp.status_code == 429:
        return ""
    if resp.status_code == 403:
        logger.warning("BD_UNLOCKER listing 403 url=%s", url)
        return ""
    resp.raise_for_status()
    body = resp.text
    _write_cache(url, {"body": body, "fetched_at": time.time()})
    return body


def parse_offers_from_listing_html(html: str, limit: int = 20) -> List[OfferCandidate]:
    offers: List[OfferCandidate] = []
    if not html:
        return offers
    try:
        state = _extract_listing_state(html)
    except Exception:
        return offers
    elements = state.get("items", {}).get("elements", [])
    for item in elements:
        if len(offers) >= limit:
            break
        price_val, currency = _extract_price_currency(item)
        url = _normalize_url(item.get("url") or item.get("product", {}).get("url"))
        offer_id = _extract_offer_id(item, url)
        seller = None
        try:
            seller = item.get("seller", {}).get("login")
        except Exception:
            seller = None
        offers.append(
            OfferCandidate(
                price=Decimal(str(price_val)) if price_val is not None else None,
                currency=currency,
                url=url,
                offer_id=offer_id,
                seller=seller,
            )
        )
    # sort by price asc, None last
    offers = sorted(offers, key=lambda o: (Decimal("1e9") if o.price is None else o.price))
    return offers


async def fetch_offer_html_via_unlocker(url: str) -> str:
    if not url:
        return ""
    cached = _read_cache(url)
    if cached and cached.get("body"):
        return cached["body"]
    _wait_for_rate_limit()
    async with httpx.AsyncClient(timeout=_http_timeout(), proxy=_resi_proxy_url(), verify=_resi_verify()) as client:
        try:
            async with rate_limiter.throttle(url):
                resp = await client.get(url)
        except RateLimited:
            raise
        except Exception as exc:
            logger.warning("BD_UNLOCKER offer fetch failed url=%s err=%r", url, exc, exc_info=True)
            raise
    if resp.status_code == 429:
        return ""
    resp.raise_for_status()
    body = resp.text
    _write_cache(url, {"body": body, "fetched_at": time.time()})
    return body


def parse_sold_count_from_offer_html(html: str) -> SoldCountResult:
    if not html:
        return SoldCountResult(sold_count=None, status="blocked", reason="empty_or_429")
    lowered = html.lower()
    if "datadome" in lowered:
        return SoldCountResult(sold_count=None, status="blocked", reason="datadome")
    if "429" in lowered or "too many requests" in lowered:
        return SoldCountResult(sold_count=None, status="blocked", reason="429")
    # regex heurystyki: sprzedano X, liczba sprzedanych ofert X
    match = re.search(r"sprzedano\s*([0-9][0-9\s]*)", lowered, re.IGNORECASE)
    sold_count = None
    if match:
        try:
            sold_count = int(match.group(1).replace(" ", ""))
        except Exception:
            sold_count = None
    if sold_count is None:
        match2 = re.search(r"liczba\s+sprzedanych\s+ofert[^0-9]*([0-9][0-9\s]*)", lowered, re.IGNORECASE)
        if match2:
            try:
                sold_count = int(match2.group(1).replace(" ", ""))
            except Exception:
                sold_count = None
    if sold_count is not None:
        return SoldCountResult(sold_count=sold_count, status="visible")
    return SoldCountResult(sold_count=None, status="missing")
