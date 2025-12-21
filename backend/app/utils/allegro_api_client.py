from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult

API_URL = "https://api.allegro.pl/public/allegro-offers"  # placeholder endpoint


async def fetch_from_allegro_api(ean: str) -> AllegroResult:
    now = datetime.now(timezone.utc)
    token = settings.ALLEGRO_API_TOKEN
    if not token:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "missing_token", "source": "api"},
            source="api",
            last_checked_at=now,
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.proxy_timeout) as client:
            resp = await client.get(API_URL, params={"ean": ean}, headers=headers)
    except Exception as exc:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": str(exc), "source": "api"},
            source="api",
            last_checked_at=now,
        )

    payload: Dict[str, Any] = {"status_code": resp.status_code}
    try:
        payload_body = resp.json()
        payload["body"] = payload_body
    except Exception:
        payload["body"] = resp.text

    if resp.status_code == 404:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="api",
            last_checked_at=now,
        )

    if resp.status_code == 200:
        offers = (payload.get("body") or {}).get("offers") if isinstance(payload.get("body"), dict) else []
        price = None
        sold_count = None
        try:
            if offers:
                price_value = offers[0].get("price")
                sold_value = offers[0].get("soldCount")
                price = Decimal(str(price_value)) if price_value is not None else None
                sold_count = int(sold_value) if sold_value is not None else None
        except (InvalidOperation, ValueError, TypeError):
            price = None
            sold_count = None

        return AllegroResult(
            price=price,
            sold_count=sold_count,
            is_not_found=price is None and sold_count is None,
            is_temporary_error=False,
            raw_payload=payload,
            source="api",
            last_checked_at=now,
        )

    if resp.status_code in (429, 500, 502, 503, 504):
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload,
            source="api",
            last_checked_at=now,
        )

    return AllegroResult(
        price=None,
        sold_count=None,
        is_not_found=False,
        is_temporary_error=True,
        raw_payload=payload,
        source="api",
        last_checked_at=now,
    )
