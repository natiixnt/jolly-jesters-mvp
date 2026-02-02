from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult
from app.utils.rate_limiter import RateLimited, rate_limiter

logger = logging.getLogger(__name__)


def _local_scraper_timeout() -> httpx.Timeout:
    timeout_seconds = max(5.0, float(settings.local_scraper_timeout or 0))
    connect_timeout = min(10.0, timeout_seconds)
    read_timeout = min(timeout_seconds, max(5.0, timeout_seconds - 1.0))
    return httpx.Timeout(
        timeout_seconds,
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )


def _local_scraper_max_request_seconds() -> float:
    try:
        return max(10.0, float(os.getenv("LOCAL_SCRAPER_MAX_REQUEST_SECONDS", "120")))
    except Exception:
        return 120.0


def _local_scraper_attempts() -> int:
    try:
        retries = int(settings.scraping_retries)
    except Exception:
        retries = 0
    return max(1, retries + 1)


def _local_scraper_backoff(attempt: int) -> float:
    return min(0.2 * (2 ** (attempt - 1)), 1.0)


def _local_scraper_base_url() -> Optional[str]:
    url = settings.LOCAL_SCRAPER_URL
    if not url:
        return None
    base = url.strip().rstrip("/")
    if base.lower().endswith("/scrape"):
        base = base[: -len("/scrape")]
    return base


def build_local_scraper_url(path: str) -> Optional[str]:
    base_url = _local_scraper_base_url()
    if not base_url:
        return None
    return f"{base_url}/{path.lstrip('/')}"


def check_local_scraper_health(timeout_seconds: float = 2.0) -> Dict[str, Any]:
    url = build_local_scraper_url("health")
    if not settings.LOCAL_SCRAPER_ENABLED:
        return {"enabled": False, "url": url, "status": "disabled"}
    if not url:
        return {"enabled": True, "url": None, "status": "missing_url"}
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(url)
    except Exception as exc:
        return {"enabled": True, "url": url, "status": "error", "error": repr(exc)}
    return {
        "enabled": True,
        "url": url,
        "status": "ok" if resp.status_code < 400 else "error",
        "status_code": resp.status_code,
    }


def update_local_scraper_windows(local_scraper_windows: int, timeout_seconds: float = 2.0) -> Dict[str, Any]:
    url = build_local_scraper_url("config")
    if not settings.LOCAL_SCRAPER_ENABLED:
        return {"enabled": False, "url": url, "status": "disabled"}
    if not url:
        return {"enabled": True, "url": None, "status": "missing_url"}
    payload = {"local_scraper_windows": max(1, int(local_scraper_windows or 1))}
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.put(url, json=payload)
    except Exception as exc:
        logger.warning(
            "Local scraper config update failed url=%s err=%r",
            url,
            exc,
            exc_info=True,
        )
        return {"enabled": True, "url": url, "status": "error", "error": repr(exc)}
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    return {
        "enabled": True,
        "url": url,
        "status": "ok" if resp.status_code < 400 else "error",
        "status_code": resp.status_code,
        "data": data,
    }


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        val_str = str(value)
        if val_str.endswith("Z"):
            val_str = val_str.replace("Z", "+00:00")
        return datetime.fromisoformat(val_str)
    except Exception:
        return None


async def fetch_via_local_scraper(ean: str) -> AllegroResult:
    url = build_local_scraper_url("scrape")
    if not url:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "local_scraper_disabled", "source": "local"},
            source="local",
            fingerprint_id=None,
        )

    start_ts = time.monotonic()
    max_request_seconds = _local_scraper_max_request_seconds()
    try:
        logger.info("Local scraper request ean=%s url=%s", ean, url)
        timeout = _local_scraper_timeout()
        attempts = _local_scraper_attempts()
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(1, attempts + 1):
                try:
                    async with rate_limiter.throttle("https://allegro.pl"):
                        resp = await client.post(url, json={"ean": ean}, timeout=max_request_seconds)
                    break
                except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                    duration = time.monotonic() - start_ts
                    if attempt >= attempts:
                        raise
                    logger.warning(
                        "Local scraper retry ean=%s attempt=%s/%s type=%s stage=request_timeout duration=%.2fs",
                        ean,
                        attempt,
                        attempts,
                        type(exc).__name__,
                        duration,
                    )
                    await asyncio.sleep(_local_scraper_backoff(attempt))
                except httpx.ConnectError as exc:
                    duration = time.monotonic() - start_ts
                    if attempt >= attempts:
                        raise
                    logger.warning(
                        "Local scraper retry ean=%s attempt=%s/%s type=%s stage=connect duration=%.2fs",
                        ean,
                        attempt,
                        attempts,
                        type(exc).__name__,
                        duration,
                    )
                    await asyncio.sleep(_local_scraper_backoff(attempt))
    except RateLimited:
        # Should not happen often; return blocked signal
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": "blocked",
                "provider": "local",
                "block_reason": "blocked_429_cooldown",
            },
            source="local",
            blocked=True,
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        duration = time.monotonic() - start_ts
        logger.warning(
            "Local scraper network error for ean=%s type=%s err=%r duration=%.2fs stage=%s",
            ean,
            type(exc).__name__,
            exc,
            duration,
            "connect" if isinstance(exc, httpx.ConnectError) else "timeout",
            exc_info=True,
        )
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error",
                "error_type": type(exc).__name__,
                "error_detail": repr(exc),
                "source": "local",
                "url": url,
                "duration_seconds": round(duration, 2),
            },
            error="timeout" if isinstance(exc, httpx.TimeoutException) else "network_error",
            source="local",
            fingerprint_id=None,
        )
    except Exception as exc:
        duration = time.monotonic() - start_ts
        logger.warning(
            "Local scraper network error for ean=%s type=%s err=%r duration=%.2fs stage=unknown",
            ean,
            type(exc).__name__,
            exc,
            duration,
            exc_info=True,
        )
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": repr(exc),
                "error_type": type(exc).__name__,
                "source": "local",
                "url": url,
                "duration_seconds": round(duration, 2),
            },
            source="local",
            fingerprint_id=None,
        )

    duration = time.monotonic() - start_ts
    logger.info("Local scraper response ean=%s status=%s duration=%.2fs", ean, resp.status_code, duration)

    try:
        payload: Dict[str, Any] = resp.json()
    except Exception as exc:
        payload = {"body": resp.text, "error": f"invalid_json: {exc}"}

    if not isinstance(payload, dict):
        payload = {"body": str(payload)}

    payload.setdefault("status_code", resp.status_code)
    payload.setdefault("url", url)
    payload["source"] = payload.get("source") or "local_scraper"
    payload.setdefault("request_duration_seconds", round(duration, 2))

    if resp.status_code >= 400:
        payload.setdefault("error", f"http_{resp.status_code}")
        if resp.text:
            payload["response_text"] = resp.text[:1000]
        logger.warning(
            "Local scraper HTTP error ean=%s status=%s body=%s",
            ean,
            resp.status_code,
            (resp.text or "")[:200],
        )

    source_label = payload.get("source") or "local_scraper"
    scraped_at = _parse_datetime(payload.get("scraped_at"))
    blocked = bool(payload.get("blocked"))
    error_message = payload.get("error")

    if resp.status_code == 404 or payload.get("not_found"):
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source=source_label,
            last_checked_at=scraped_at,
            blocked=blocked,
            fingerprint_id=payload.get("fingerprint_id"),
        )

    price_val = payload.get("lowest_price") if "lowest_price" in payload else payload.get("price")
    sold_val = (
        payload.get("offers_total_sold_count")
        if payload.get("offers_total_sold_count") is not None
        else payload.get("sold_count") or payload.get("category_sold_count")
    )

    try:
        price = Decimal(str(price_val)) if price_val is not None else None
    except (InvalidOperation, ValueError):
        price = None

    try:
        sold_count: Optional[int] = int(sold_val) if sold_val is not None else None
    except (TypeError, ValueError):
        sold_count = None

    has_data = bool(payload.get("offers")) or price_val is not None or sold_val is not None
    if not has_data and not payload.get("not_found") and not error_message:
        payload["error"] = "empty_payload"
        error_message = payload["error"]

    if resp.status_code >= 400 or blocked or (error_message and not payload.get("not_found")):
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload | {"status_code": resp.status_code, "source": "local"},
            source=source_label,
            last_checked_at=scraped_at,
            blocked=blocked,
            fingerprint_id=payload.get("fingerprint_id"),
        )

    return AllegroResult(
        price=price,
        sold_count=sold_count,
        is_not_found=False,
        is_temporary_error=False,
        raw_payload=payload,
        source=source_label,
        last_checked_at=scraped_at,
        product_title=payload.get("product_title"),
        product_url=payload.get("product_url"),
        offers=payload.get("offers"),
        blocked=blocked,
        fingerprint_id=payload.get("fingerprint_id"),
    )
