from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import requests

from app.core.config import settings


@dataclass
class AllegroScrapeResult:
    price: Optional[Decimal]
    sold_count: Optional[int]
    is_not_found: bool
    raw_payload: dict
    source: str = "scraping"


def scrape_listing(ean: str) -> AllegroScrapeResult:
    """Very small HTTP scraper placeholder.

    The implementation intentionally avoids aggressive parsing; it only
    checks whether the Allegro listing search page is reachable. Real
    parsing can be added later without changing the public interface.
    """

    url = f"https://allegro.pl/listing"
    params = {"string": ean}

    proxies = None
    if settings.proxy_list:
        proxies = {"http": settings.proxy_list[0], "https": settings.proxy_list[0]}

    try:
        resp = requests.get(url, params=params, timeout=settings.proxy_timeout, proxies=proxies)
        not_found = resp.status_code == 404
        payload = {"status": resp.status_code}
    except Exception as exc:
        not_found = True
        payload = {"error": str(exc)}

    return AllegroScrapeResult(
        price=None,
        sold_count=None,
        is_not_found=not_found,
        raw_payload=payload,
    )
