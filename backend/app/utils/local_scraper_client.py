from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

import anyio
import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
SCRAPER_TIMEOUT = 10.0  # seconds
HEALTH_TIMEOUT = 1.0


def _normalize_base_url(raw_url: str) -> str:
    """
    Normalise configured scraper base URL so users can pass either
    http://host:5050 or http://host:5050/scrape without producing
    a double /scrape segment.
    """
    base = raw_url.strip().rstrip("/")
    if base.lower().endswith("/scrape"):
        base = base[: -len("/scrape")]
    return base


def _build_scrape_url() -> str:
    base_url = _normalize_base_url(settings.LOCAL_SCRAPER_URL)
    return f"{base_url}/scrape"


def _classify_exception(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connection_error"
    return "http_error"


def check_local_scraper_health(timeout: float | None = None) -> Dict[str, Any]:
    if not settings.LOCAL_SCRAPER_URL:
        raise RuntimeError("LOCAL_SCRAPER_URL is not configured")

    url = f"{_normalize_base_url(settings.LOCAL_SCRAPER_URL)}/health"
    try:
        resp = httpx.get(url, timeout=timeout or HEALTH_TIMEOUT)
    except Exception as exc:
        raise RuntimeError(f"Local scraper healthcheck failed: {exc}")

    if resp.status_code >= 400:
        raise RuntimeError(f"Local scraper healthcheck returned {resp.status_code} from {url}")

    try:
        payload: Dict[str, Any] = resp.json()
    except Exception:
        payload = {}

    if payload.get("status") != "ok":
        raise RuntimeError(f"Local scraper healthcheck unexpected payload from {url}: {payload}")

    return payload


async def fetch_via_local_scraper(ean: str) -> AllegroResult:
    if not settings.LOCAL_SCRAPER_URL:
        logger.warning("LOCAL_SCRAPER_URL is not configured while local scraper was requested (ean=%s)", ean)
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "local_scraper_disabled", "source": "local"},
            source="local",
        )

    url = _build_scrape_url()

    resp = None
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Calling local scraper (attempt %s/%s): %s ean=%s", attempt, MAX_RETRIES, url, ean)
            async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT) as client:
                resp = await client.post(url, json={"ean": ean})
            break
        except Exception as exc:
            last_exc = exc
            logger.error(
                "Local scraper call failed (attempt %s/%s ean=%s url=%s): %s",
                attempt,
                MAX_RETRIES,
                ean,
                url,
                exc,
            )
            if attempt < MAX_RETRIES:
                await anyio.sleep(0.3)

    if resp is None:
        error_type = _classify_exception(last_exc) if last_exc else "unknown"
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": str(last_exc) if last_exc else "unknown_error",
                "error_type": error_type,
                "source": "local",
                "url": url,
            },
            source="local",
        )

    try:
        payload: Dict[str, Any] = resp.json()
    except Exception:
        payload = {"body": resp.text}

    if not isinstance(payload, dict):
        payload = {"body": str(payload)}

    payload.setdefault("status_code", resp.status_code)
    payload.setdefault("url", url)
    payload["source"] = payload.get("source") or "local"

    if resp.status_code >= 400:
        logger.warning(
            "Local scraper returned error status (ean=%s url=%s status=%s payload=%s)",
            ean,
            url,
            resp.status_code,
            str(payload)[:500],
        )

    if resp.status_code == 404 or payload.get("not_found"):
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="local_scraper",
        )

    if resp.status_code >= 400:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload | {"error_type": "http_error"},
            source="local",
        )

    price_val = payload.get("price")
    sold_val = payload.get("sold_count")

    try:
        price = Decimal(str(price_val)) if price_val is not None else None
    except (InvalidOperation, ValueError):
        price = None

    try:
        sold_count: Optional[int] = int(sold_val) if sold_val is not None else None
    except (TypeError, ValueError):
        sold_count = None

    if resp.status_code >= 500:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload | {"status_code": resp.status_code, "source": "local", "error_type": "http_error"},
            source="local",
        )

    return AllegroResult(
        price=price,
        sold_count=sold_count,
        is_not_found=False,
        is_temporary_error=False,
        raw_payload=payload,
        source="local",
    )
