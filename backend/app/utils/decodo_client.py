"""Decodo Web Scraping API client used as an alternate Allegro scraper.

Flow:
1) Build listing URL from EAN.
2) Fetch listing HTML via Decodo and pick the lowest Buy Now offer.
3) Fetch that offer page via Decodo and extract price + sold_count.

The public contract mirrors other scrapers by returning AllegroResult objects.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

import httpx

from app.services.schemas import AllegroResult
from app.utils.allegro_scraper_http import _extract_listing_state, _listing_elements_count
from app.utils.bd_unlocker_client import parse_sold_count_from_offer_html

logger = logging.getLogger(__name__)

DECODO_ENDPOINT_DEFAULT = "https://scraper-api.decodo.com/v2/scrape"
_BLOCKED_MARKERS = ("captcha", "access denied", "cloudflare", "attention required")


# ---------- Config helpers ----------


def _decodo_endpoint() -> str:
    return os.getenv("DECODO_ENDPOINT", DECODO_ENDPOINT_DEFAULT)


def _decodo_token() -> Optional[str]:
    return os.getenv("DECODO_TOKEN")


def _decodo_geo() -> str:
    return os.getenv("DECODO_GEO", "")


def _decodo_locale() -> str:
    return os.getenv("DECODO_LOCALE", "pl-pl")


def _decodo_headless() -> str:
    return os.getenv("DECODO_HEADLESS", "html")


def _decodo_device_type() -> str:
    return os.getenv("DECODO_DEVICE_TYPE", "desktop")


def _decodo_xhr() -> bool:
    raw = os.getenv("DECODO_XHR", "false")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _decodo_session_id_mode() -> str:
    return os.getenv("DECODO_SESSION_ID_MODE", "off").strip().lower()


def _maybe_session_id(ean: str) -> Optional[str]:
    mode = _decodo_session_id_mode()
    if mode == "per_ean":
        return hashlib.sha1(ean.encode("utf-8")).hexdigest()
    return None


def _decodo_timeout() -> httpx.Timeout:
    total = max(5.0, float(os.getenv("DECODO_TIMEOUT_SECONDS", os.getenv("DECODO_TIMEOUT", "150"))))
    connect = min(total, max(3.0, total / 3))
    read = min(total, max(5.0, total * 0.8))
    return httpx.Timeout(timeout=total, connect=connect, read=read, write=read, pool=connect)


def _decodo_max_attempts() -> int:
    try:
        return min(5, max(1, int(os.getenv("DECODO_MAX_RETRIES", "4"))))
    except Exception:
        return 4


def _decodo_backoff(attempt: int) -> float:
    try:
        base = float(os.getenv("DECODO_BACKOFF_BASE", "1.5"))
    except Exception:
        base = 1.5
    jitter = random.uniform(0.2, 0.8)
    return min(12.0, base * (2 ** max(0, attempt - 1)) + jitter)


def _request_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Basic {token}"
    return headers


def _build_payload(url: str, session_id: Optional[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "url": url,
        "headless": _decodo_headless(),
        "device_type": _decodo_device_type(),
        "locale": _decodo_locale(),
        "xhr": _decodo_xhr(),
        "successful_status_codes": [200, 403],
        "headers": [
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept-Language: pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
        ],
        "force_headers": True,
    }
    geo = _decodo_geo()
    if geo:
        payload["geo"] = geo
    if session_id:
        payload["session_id"] = session_id
    return payload


# ---------- HTML extraction helpers ----------


def _extract_html_from_response(resp: httpx.Response) -> Tuple[Optional[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"status_code": resp.status_code}
    content_type = resp.headers.get("content-type", "")
    text = resp.text

    if "application/json" in content_type.lower():
        try:
            data = resp.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            meta["raw"] = list(data.keys())
            # Decodo wraps payload inside "results" list
            results = data.get("results") or data.get("result")
            if isinstance(results, list) and results:
                first = results[0] or {}
                meta["task_id"] = first.get("task_id") or first.get("taskId")
                meta["request_id"] = first.get("request_id") or first.get("requestId")
                html = first.get("content") or first.get("html") or first.get("response")
                return html, meta
            # Some responses return content at root
            html = data.get("content") or data.get("html") or data.get("body")
            if html:
                return html, meta
        return None, meta

    # Raw HTML
    return text or None, meta


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        normalized = url
    elif url.startswith("//"):
        normalized = f"https:{url}"
    elif url.startswith("/"):
        normalized = f"https://allegro.pl{url}"
    else:
        normalized = f"https://allegro.pl/{url.lstrip('/')}"
    # strip tracking fragments
    normalized = normalized.split("#", 1)[0]
    return normalized


def _parse_price_value(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float, Decimal)):
            return Decimal(str(value))
        if isinstance(value, str):
            cleaned = value.replace("\u00a0", " ").replace(" ", "").replace("zł", "").replace("zl", "")
            cleaned = cleaned.replace(",", ".")
            return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return None


def _extract_price_currency(item: dict) -> Tuple[Optional[Decimal], Optional[str]]:
    price_obj = item.get("price") if isinstance(item, dict) else None
    candidates = []
    if isinstance(price_obj, dict):
        candidates.extend(
            [
                price_obj,
                price_obj.get("mainPrice"),
                price_obj.get("buyNow"),
                price_obj.get("withDiscount"),
            ]
        )
    selling_mode = item.get("sellingMode") if isinstance(item, dict) else None
    if isinstance(selling_mode, dict):
        candidates.append(selling_mode.get("price"))

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        amount = candidate.get("amount")
        currency = candidate.get("currency") or candidate.get("currencyCode")
        dec = _parse_price_value(amount)
        if dec is not None:
            return dec, currency
    return None, None


@dataclass
class ListingParseResult:
    offer_url: Optional[str]
    price: Optional[Decimal]
    offers_count: int
    status: str  # ok | no_offers | auctions_only | parse_error


def parse_listing_lowest_offer(html: str) -> ListingParseResult:
    offers = []
    auctions_only = False
    offers_count = 0

    if html:
        try:
            state = _extract_listing_state(html) or {}
            elements = state.get("__listing_StoreState", {}).get("items", {}).get("elements", [])
            offers_count = _listing_elements_count(state) or 0
            for el in elements:
                price, _currency = _extract_price_currency(el)
                offer_url = _normalize_url(el.get("url") or el.get("productSeoLink", {}).get("url"))
                selling_mode = str(el.get("sellingMode") or "").lower()
                is_auction = bool(el.get("isAuction") or selling_mode.startswith("auction"))
                if is_auction:
                    auctions_only = True
                if price is None or is_auction:
                    continue
                offers.append((price, offer_url))
        except Exception:
            logger.exception("DECODO parse listing_store_state failed")

    if not offers:
        # fallback: regex on raw HTML (price near /oferta/)
        pattern = re.compile(r'(/oferta/[a-z0-9\-\_]+)[^<>]{0,160}?([0-9][0-9\u00a0\s\.,]+)\s*zł', re.IGNORECASE)
        for match in pattern.finditer(html or ""):
            url = _normalize_url(match.group(1))
            price = _parse_price_value(match.group(2))
            if price is not None:
                offers.append((price, url))
        offers_count = max(offers_count, len(offers))

    if not offers:
        status = "auctions_only" if auctions_only else "no_offers"
        return ListingParseResult(offer_url=None, price=None, offers_count=offers_count, status=status)

    lowest = sorted(offers, key=lambda t: (t[0], t[1] or ""))[0]
    return ListingParseResult(offer_url=lowest[1], price=lowest[0], offers_count=offers_count or len(offers), status="ok")


@dataclass
class OfferDetails:
    price: Optional[Decimal]
    sold_count: Optional[int]
    sold_count_status: str  # ok | missing | blocked | error


_PRICE_PATTERNS = (
    re.compile(r'property=["\']product:price:amount["\']\s*content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'itemprop=["\']price["\']\s*content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'"price"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'data-testid=["\']price-value["\'][^>]*?>[^0-9]*([0-9][0-9\u00a0\s\.,]+)', re.IGNORECASE),
)


def _parse_offer_price(html: str) -> Optional[Decimal]:
    for pattern in _PRICE_PATTERNS:
        match = pattern.search(html or "")
        if match:
            price = _parse_price_value(match.group(1))
            if price is not None:
                return price
    return None


def parse_offer_details(html: str) -> OfferDetails:
    price = _parse_offer_price(html or "")
    sold_info = parse_sold_count_from_offer_html(html or "")
    sold_status = sold_info.status if hasattr(sold_info, "status") else "missing"
    sold_count = getattr(sold_info, "sold_count", None)
    if sold_status == "visible":
        sold_status = "ok"
    elif sold_status == "missing":
        sold_status = "sold_count_missing"
    return OfferDetails(price=price, sold_count=sold_count, sold_count_status=sold_status)


# ---------- HTTP caller ----------


async def _request_html(url: str, session_id: Optional[str]) -> Tuple[Optional[str], Dict[str, Any]]:
    token = _decodo_token()
    if not token:
        return None, {"error": "decodo_token_missing", "provider": "decodo"}

    headers = _request_headers(token)
    payload = _build_payload(url, session_id=session_id)
    attempts = _decodo_max_attempts()
    timeout = _decodo_timeout()
    endpoint = _decodo_endpoint()

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                resp = await client.post(endpoint, headers=headers, json=payload)
                duration = time.monotonic() - started
                html, meta = _extract_html_from_response(resp)
                meta.update(
                    {
                        "attempt": attempt,
                        "request_duration_seconds": round(duration, 2),
                        "provider": "decodo",
                        "endpoint": endpoint,
                    }
                )

                if resp.status_code in (403, 429):
                    meta["error"] = "blocked"
                    meta["block_reason"] = f"status_{resp.status_code}"
                    meta["blocked"] = True
                    return None, meta
                if resp.status_code >= 500:
                    meta["error"] = f"server_error:{resp.status_code}"
                    if attempt >= attempts:
                        return None, meta
                    await asyncio.sleep(_decodo_backoff(attempt))
                    continue
                if resp.status_code >= 400:
                    meta["error"] = f"http_{resp.status_code}"
                    return None, meta
                if not html:
                    meta["error"] = "empty_body"
                    if attempt >= attempts:
                        return None, meta
                    await asyncio.sleep(_decodo_backoff(attempt))
                    continue

                lowered = (html or "").lower()
                if any(marker in lowered for marker in _BLOCKED_MARKERS):
                    meta["error"] = "blocked"
                    meta["block_reason"] = "html_marker"
                    meta["blocked"] = True
                    return None, meta

                return html, meta
            except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                duration = time.monotonic() - started
                logger.warning("DECODO timeout url=%s attempt=%s err=%r duration=%.2fs", url, attempt, exc, duration)
                if attempt >= attempts:
                    return None, {
                        "error": "timeout",
                        "error_type": type(exc).__name__,
                        "provider": "decodo",
                        "attempt": attempt,
                        "request_duration_seconds": round(duration, 2),
                    }
                await asyncio.sleep(_decodo_backoff(attempt))
            except httpx.ConnectError as exc:
                duration = time.monotonic() - started
                logger.warning("DECODO connect_error url=%s attempt=%s err=%r duration=%.2fs", url, attempt, exc, duration)
                if attempt >= attempts:
                    return None, {
                        "error": "network_error",
                        "error_type": type(exc).__name__,
                        "provider": "decodo",
                        "attempt": attempt,
                        "request_duration_seconds": round(duration, 2),
                    }
                await asyncio.sleep(_decodo_backoff(attempt))
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("DECODO unexpected error url=%s attempt=%s err=%r", url, attempt, exc)
                return None, {"error": str(exc), "provider": "decodo"}
    return None, {"error": "unknown", "provider": "decodo"}


# ---------- Public API ----------


async def fetch_html(url: str) -> Optional[str]:
    """Fetch raw HTML for the given URL via Decodo."""
    html, _meta = await _request_html(url, session_id=None)
    return html


async def fetch_via_decodo(ean: str) -> AllegroResult:
    """Two-step Allegro scrape via Decodo."""
    now = datetime.now(timezone.utc)
    listing_url = f"https://allegro.pl/listing?string={ean}"
    session_id = _maybe_session_id(ean)

    logger.info("DECODO listing request ean=%s session_id=%s headless=%s device=%s xhr=%s geo=%s locale=%s", ean, session_id, _decodo_headless(), _decodo_device_type(), _decodo_xhr(), _decodo_geo(), _decodo_locale())

    listing_html, listing_meta = await _request_html(listing_url, session_id=session_id)
    if not listing_html:
        blocked = bool(listing_meta.get("blocked") or listing_meta.get("error") in {"blocked", "empty_body"})
        error = listing_meta.get("error")
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"provider": "decodo", "stage": "listing", **(listing_meta or {})},
            error=error,
            source="decodo",
            last_checked_at=now,
            blocked=blocked,
        )

    listing_result = parse_listing_lowest_offer(listing_html)
    if listing_result.offer_url is None:
        payload = {
            "provider": "decodo",
            "stage": "listing",
            "listing_status": listing_result.status,
            "offers_count": listing_result.offers_count,
            "session_id": session_id,
        } | (listing_meta or {})
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="decodo",
            last_checked_at=now,
            product_url=None,
        )

    offer_url = _normalize_url(listing_result.offer_url)
    offer_html, offer_meta = await _request_html(offer_url, session_id=session_id)
    if not offer_html:
        blocked = bool(
            offer_meta.get("blocked")
            or listing_meta.get("blocked")
            or offer_meta.get("error") in {"blocked", "empty_body"}
        )
        error = offer_meta.get("error")
        payload = {"provider": "decodo", "stage": "pdp", "listing_status": listing_result.status} | (
            offer_meta or {}
        )
        return AllegroResult(
            price=listing_result.price,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload,
            error=error,
            source="decodo",
            last_checked_at=now,
            product_url=offer_url,
            blocked=blocked,
        )

    offer_details = parse_offer_details(offer_html)
    final_price = offer_details.price or listing_result.price
    sold_status = offer_details.sold_count_status
    payload = {
        "provider": "decodo",
        "stage": "pdp",
        "listing_status": listing_result.status,
        "listing_price": float(listing_result.price) if listing_result.price is not None else None,
        "offers_count": listing_result.offers_count,
        "offer_url": offer_url,
        "sold_count_status": sold_status,
        "session_id": session_id,
    } | (listing_meta or {}) | (offer_meta or {})

    return AllegroResult(
        price=final_price,
        sold_count=offer_details.sold_count,
        is_not_found=final_price is None,
        is_temporary_error=False,
        raw_payload=payload,
        source="decodo",
        last_checked_at=now,
        product_url=offer_url,
    )
