"""Allegro HTML parsing + Decodo-powered scrape service."""

from __future__ import annotations

import json
import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from app.providers.decodo_client import DecodoClient
from app.services.schemas import AllegroResult
from app.utils.ean import is_valid_ean13

logger = logging.getLogger(__name__)

PRICE_REGEX = re.compile(r'([0-9][0-9\u00a0\s\.,]+)\s*zł', re.IGNORECASE)
OFFER_LINK_REGEX = re.compile(r"(https?://)?(www\.)?allegro\.pl(/oferta/[a-z0-9\-\_]+)")
OFFER_LINK_REL_REGEX = re.compile(r"(/oferta/[a-z0-9\-\_]+)")
PRODUCT_LINK_REGEX = re.compile(r"(https?://)?(www\.)?allegro\.pl(/produkt/[^\"'>\s]+?offerId=[A-Za-z0-9\-]+)")
PRODUCT_LINK_REL_REGEX = re.compile(r"(/produkt/[^\"'>\s]+?offerId=[A-Za-z0-9\-]+)")
SOLD_PATTERNS = [
    re.compile(r"sprzedano\s*([0-9][0-9\s]*)", re.IGNORECASE),
    re.compile(r"kupiono\s*([0-9][0-9\s]*)", re.IGNORECASE),
    re.compile(r"([0-9][0-9\s]*)\s*os[oó]b\s*kupi[łl]o", re.IGNORECASE),
    re.compile(r"liczba\s+sprzedanych\s+ofert[^0-9]*([0-9][0-9\s]*)", re.IGNORECASE),
]

def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_FAIL_CACHE: Dict[str, float] = {}
DECODO_SEM = asyncio.Semaphore(_env_int("DECODO_CONCURRENCY", 2))


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
    return normalized.split("#", 1)[0]


def extract_candidate_offer_urls(listing_html: str) -> List[str]:
    urls: List[str] = []
    seen = set()
    for regex in (OFFER_LINK_REGEX, OFFER_LINK_REL_REGEX, PRODUCT_LINK_REGEX, PRODUCT_LINK_REL_REGEX):
        for match in regex.finditer(listing_html or ""):
            groups = match.groups()
            raw = groups[-1] if groups else None
            url = _normalize_url(raw)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _parse_price_value(value: str) -> Optional[Decimal]:
    cleaned = value.replace("\u00a0", " ").replace(" ", "").replace("zł", "").replace("zl", "")
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _extract_from_json_ld(html: str) -> Optional[Decimal]:
    for match in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html or "", re.DOTALL | re.IGNORECASE):
        raw = match.group(1)
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates = []
        if isinstance(data, list):
            candidates.extend(data)
        else:
            candidates.append(data)
        for node in candidates:
            offers = node.get("offers") if isinstance(node, dict) else None
            if isinstance(offers, dict):
                price_val = offers.get("price")
                if price_val is not None:
                    dec = _parse_price_value(str(price_val))
                    if dec is not None:
                        return dec
    return None


def extract_price(html: str) -> Optional[Decimal]:
    price = _extract_from_json_ld(html or "")
    if price is not None:
        return price

    meta_patterns = (
        re.compile(r'property=["\']product:price:amount["\']\s*content=["\']([^"\']+)["\']', re.IGNORECASE),
        re.compile(r'itemprop=["\']price["\']\s*content=["\']([^"\']+)["\']', re.IGNORECASE),
    )
    for pattern in meta_patterns:
        match = pattern.search(html or "")
        if match:
            price = _parse_price_value(match.group(1))
            if price is not None:
                return price

    match = PRICE_REGEX.search(html or "")
    if match:
        return _parse_price_value(match.group(1))
    return None


def extract_sold_count(html: str) -> Optional[int]:
    lowered = (html or "").lower()
    for pattern in SOLD_PATTERNS:
        match = pattern.search(lowered)
        if match:
            try:
                return int(match.group(1).replace(" ", ""))
            except Exception:
                continue
    return None


def _save_debug_html(name: str, content: str) -> Optional[str]:
    if not _bool_env("SAVE_DEBUG_HTML", False):
        return None
    debug_dir = Path(settings.data_root) / "data" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / name
    try:
        path.write_text(content, encoding="utf-8")
    except Exception:
        logger.warning("Failed to write debug HTML path=%s", path)
        return None
    return str(path)


def _resolve_session_id(ean: str) -> Optional[str]:
    mode = (os.getenv("DECODO_SESSION_ID_MODE") or "").strip().lower()
    static_session = os.getenv("DECODO_SESSION_ID")
    if static_session:
        return static_session
    if mode == "per_ean":
        import hashlib

        return hashlib.sha1(ean.encode("utf-8")).hexdigest()
    return None


@dataclass
class OfferCandidateResult:
    url: str
    price: Decimal
    sold_count: Optional[int]
    raw_meta: dict


async def choose_lowest_offer(
    ean: str,
    client: Optional[DecodoClient] = None,
    max_candidates: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> AllegroResult:
    """Main flow: listing -> candidate URLs -> offer pages -> choose lowest price."""
    client = client or DecodoClient()
    if not is_valid_ean13(ean):
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=False,
            raw_payload={"provider": "decodo", "stage": "validation", "error": "invalid_ean"},
            error="invalid_ean",
            source="decodo",
            last_checked_at=datetime.now(timezone.utc),
        )

    skip_seconds = _fail_cache_should_skip(ean)
    if skip_seconds:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "provider": "decodo",
                "stage": "validation",
                "error": "fail_cache_recent",
                "retry_after_seconds": round(skip_seconds, 2),
            },
            error="fail_cache_recent",
            source="decodo",
            last_checked_at=datetime.now(timezone.utc),
            blocked=False,
        )

    max_candidates = max_candidates or _env_int("DECODO_MAX_CANDIDATES", 3)
    timeout_seconds = timeout_seconds or _env_float("DECODO_EAN_TIMEOUT_SECONDS", 60.0)

    started = time.monotonic()
    session_id = _resolve_session_id(ean)
    listing_url = f"https://allegro.pl/listing?string={ean}"

    async with DECODO_SEM:
        listing_fetch = await client.fetch_html(listing_url, session_id=session_id)
    if listing_fetch.html:
        _save_debug_html(f"debug_listing_{ean}.html", listing_fetch.html)

    if not listing_fetch.html:
        _fail_cache_record(ean)
        payload = {"provider": "decodo", "stage": "listing", "failure": "provider_faulted", **(listing_fetch.meta or {})}
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload,
            error=listing_fetch.error,
            source="decodo",
            last_checked_at=datetime.now(timezone.utc),
            blocked=bool(listing_fetch.blocked),
        )

    candidates = extract_candidate_offer_urls(listing_fetch.html)
    total_candidates = len(candidates)
    if not candidates:
        payload = {
            "provider": "decodo",
            "stage": "listing",
            "listing_status": "no_candidates",
            "failure": "no_candidates",
            "candidate_count": 0,
        } | (listing_fetch.meta or {})
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="decodo",
            last_checked_at=datetime.now(timezone.utc),
        )

    candidates = candidates[:max_candidates]
    offers: List[OfferCandidateResult] = []
    blocked_any = bool(listing_fetch.blocked)
    temp_errors = 0

    for idx, url in enumerate(candidates, start=1):
        if time.monotonic() - started >= timeout_seconds:
            logger.warning("DECODO timeout budget exceeded ean=%s after %s seconds", ean, timeout_seconds)
            _fail_cache_record(ean)
            break
        async with DECODO_SEM:
            offer_fetch = await client.fetch_html(url, session_id=session_id)
        if offer_fetch.html:
            _save_debug_html(f"debug_offer_{ean}_{idx}.html", offer_fetch.html)
        if offer_fetch.blocked:
            blocked_any = True
        if not offer_fetch.html:
            temp_errors += 1
            continue
        price = extract_price(offer_fetch.html)
        sold_count = extract_sold_count(offer_fetch.html)
        if price is None:
            continue
        offers.append(
            OfferCandidateResult(
                url=url,
                price=price,
                sold_count=sold_count,
                raw_meta=offer_fetch.meta,
            )
        )

    if not offers:
        _fail_cache_record(ean)
        payload = {
            "provider": "decodo",
            "stage": "offer",
            "candidate_count": total_candidates,
            "processed_candidates": len(candidates),
            "temp_errors": temp_errors,
            "blocked": blocked_any,
            "failure": "budget_exceeded" if time.monotonic() - started >= timeout_seconds else "no_price",
        } | (listing_fetch.meta or {})
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False if blocked_any else True,
            is_temporary_error=bool(blocked_any or temp_errors or time.monotonic() - started >= timeout_seconds),
            raw_payload=payload,
            error="blocked" if blocked_any else payload.get("failure"),
            source="decodo",
            last_checked_at=datetime.now(timezone.utc),
            blocked=blocked_any,
        )

    offers_sorted = sorted(offers, key=lambda o: (o.price, candidates.index(o.url)))
    winner = offers_sorted[0]
    payload = {
        "provider": "decodo",
        "stage": "offer",
        "listing_candidate_count": total_candidates,
        "processed_candidates": len(candidates),
        "offer_url": winner.url,
        "blocked": blocked_any,
        "sold_count_status": "ok" if winner.sold_count is not None else "missing",
    } | (winner.raw_meta or {}) | (listing_fetch.meta or {})

    return AllegroResult(
        price=winner.price,
        sold_count=winner.sold_count,
        is_not_found=False,
        is_temporary_error=False,
        raw_payload=payload,
        source="decodo",
        last_checked_at=datetime.now(timezone.utc),
        product_url=winner.url,
        blocked=blocked_any,
    )
DECODO_SEM = asyncio.Semaphore(_env_int("DECODO_CONCURRENCY", 2))


def _fail_cache_hours() -> float:
    try:
        return float(os.getenv("DECODO_FAIL_CACHE_HOURS", "6"))
    except Exception:
        return 6.0


def _fail_cache_should_skip(ean: str) -> Optional[float]:
    ttl_hours = _fail_cache_hours()
    if ttl_hours <= 0:
        return None
    ttl_seconds = ttl_hours * 3600
    ts = _FAIL_CACHE.get(ean)
    if ts is None:
        return None
    remaining = ttl_seconds - (time.monotonic() - ts)
    if remaining > 0:
        return remaining
    _FAIL_CACHE.pop(ean, None)
    return None


def _fail_cache_record(ean: str) -> None:
    ttl_hours = _fail_cache_hours()
    if ttl_hours <= 0:
        return
    _FAIL_CACHE[ean] = time.monotonic()
