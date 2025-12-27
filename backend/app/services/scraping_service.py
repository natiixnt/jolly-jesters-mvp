from __future__ import annotations

import logging

from app.core.config import settings
from app.services.schemas import AllegroResult, ScrapingStrategyConfig
from app.utils.allegro_api_client import fetch_from_allegro_api
from app.utils.allegro_scraper_http import fetch_via_http_scraper
from app.utils.local_scraper_client import fetch_via_local_scraper

logger = logging.getLogger(__name__)


async def fetch_allegro_data(ean: str, strategy: ScrapingStrategyConfig) -> AllegroResult:
    """Fetch Allegro data using configured strategy order."""

    # 1. Allegro API
    if strategy.use_api and settings.ALLEGRO_API_TOKEN:
        result = await fetch_from_allegro_api(ean)
        if not result.is_temporary_error:
            return result

    # 2. Cloud HTTP scraping
    if strategy.use_cloud_http:
        result = await fetch_via_http_scraper(ean)
        if not result.is_temporary_error:
            return result

    # 3. Local Selenium scraper
    if (
        strategy.use_local_scraper
        and settings.LOCAL_SCRAPER_ENABLED
        and settings.LOCAL_SCRAPER_URL
    ):
        result = await fetch_via_local_scraper(ean)
        return result
    elif strategy.use_local_scraper and not settings.LOCAL_SCRAPER_ENABLED:
        logger.warning(
            "Local scraper strategy requested for ean=%s but LOCAL_SCRAPER_ENABLED is false. "
            "Set LOCAL_SCRAPER_ENABLED=true and expose the host scraper on LOCAL_SCRAPER_URL.",
            ean,
        )

    return AllegroResult(
        price=None,
        sold_count=None,
        is_not_found=False,
        is_temporary_error=True,
        raw_payload={"error": "all_strategies_failed_or_disabled", "ean": ean},
    )
