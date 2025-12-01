from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings
from app.utils.allegro_api_client import AllegroAPIClient
from app.utils.allegro_scraper_http import AllegroScrapeResult, scrape_listing


@dataclass
class AllegroResult:
    price: Decimal | None
    sold_count: int | None
    is_not_found: bool
    raw_payload: dict
    source: str


def _from_scrape(scrape: AllegroScrapeResult) -> AllegroResult:
    return AllegroResult(
        price=scrape.price,
        sold_count=scrape.sold_count,
        is_not_found=scrape.is_not_found,
        raw_payload=scrape.raw_payload,
        source=scrape.source,
    )


def fetch_allegro_data(ean: str) -> AllegroResult:
    """Try Allegro API first, then fallback to HTTP scraping."""

    api_client = AllegroAPIClient()
    if settings.allegro_api_token:
        api_result = api_client.fetch_by_ean(ean)
        if api_result:
            return AllegroResult(
                price=api_result.price,
                sold_count=api_result.sold_count,
                is_not_found=api_result.price is None and api_result.sold_count is None,
                raw_payload=api_result.raw_payload,
                source="api",
            )

    scrape_result = scrape_listing(ean)
    return _from_scrape(scrape_result)
