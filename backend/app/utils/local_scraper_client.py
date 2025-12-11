from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult


async def fetch_via_local_scraper(ean: str) -> AllegroResult:
    if not settings.LOCAL_SCRAPER_URL:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "local_scraper_disabled", "source": "local_scraper"},
            source="local_scraper",
        )

    try:
        base_url = settings.LOCAL_SCRAPER_URL.rstrip("/")
        url = f"{base_url}/scrape"
        async with httpx.AsyncClient(timeout=settings.proxy_timeout) as client:
            resp = await client.post(url, json={"ean": ean})
    except Exception as exc:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": str(exc), "source": "local_scraper"},
            source="local_scraper",
        )

    try:
        payload: Dict[str, Any] = resp.json()
    except Exception:
        payload = {"body": resp.text}

    if resp.status_code == 404 or payload.get("not_found"):
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="local_scraper",
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
            raw_payload=payload | {"status_code": resp.status_code, "source": "local_scraper"},
            source="local_scraper",
        )

    return AllegroResult(
        price=price,
        sold_count=sold_count,
        is_not_found=False,
        is_temporary_error=False,
        raw_payload=payload,
        source="local_scraper",
    )
