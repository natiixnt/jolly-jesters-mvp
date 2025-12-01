from __future__ import annotations

import random
from decimal import Decimal
from typing import Dict

import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult


async def fetch_via_http_scraper(ean: str) -> AllegroResult:
    url = "https://allegro.pl/listing"
    params = {"string": ean}

    proxy = None
    if settings.proxy_list:
        proxy = random.choice(settings.proxy_list)

    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    try:
        async with httpx.AsyncClient(timeout=settings.proxy_timeout, proxies=proxies) as client:
            resp = await client.get(url, params=params)
    except Exception as exc:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": str(exc), "source": "cloud_http"},
            source="cloud_http",
        )

    payload: Dict[str, object] = {"status_code": resp.status_code}
    try:
        payload["body_snippet"] = resp.text[:500]
    except Exception:
        pass

    if resp.status_code == 404:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="cloud_http",
        )

    if resp.status_code in (403, 429) or resp.status_code >= 500:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload,
            source="cloud_http",
        )

    # No HTML parsing yet - treat as temporary to allow local scraper fallback
    return AllegroResult(
        price=None,
        sold_count=None,
        is_not_found=False,
        is_temporary_error=True,
        raw_payload=payload | {"note": "unparsed_html", "source": "cloud_http"},
        source="cloud_http",
    )
