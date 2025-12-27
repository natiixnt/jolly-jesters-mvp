from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult

logger = logging.getLogger(__name__)


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
    if not settings.LOCAL_SCRAPER_URL:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "local_scraper_disabled", "source": "local"},
            source="local",
        )

    try:
        base_url = settings.LOCAL_SCRAPER_URL.rstrip("/")
        url = f"{base_url}/scrape"
        logger.info("Local scraper request ean=%s url=%s", ean, url)
        async with httpx.AsyncClient(timeout=settings.local_scraper_timeout) as client:
            resp = await client.post(url, json={"ean": ean})
    except Exception as exc:
        logger.warning(
            "Local scraper network error for ean=%s type=%s err=%r",
            ean,
            type(exc).__name__,
            exc,
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
            },
            source="local",
        )

    logger.info("Local scraper response ean=%s status=%s", ean, resp.status_code)

    try:
        payload: Dict[str, Any] = resp.json()
    except Exception as exc:
        payload = {"body": resp.text, "error": f"invalid_json: {exc}"}

    payload["status_code"] = resp.status_code
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
    )
