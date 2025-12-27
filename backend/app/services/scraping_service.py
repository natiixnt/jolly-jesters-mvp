from __future__ import annotations

import logging

from app.core.config import settings
from app.services.schemas import AllegroResult, ScrapingStrategyConfig
from app.utils.allegro_scraper_http import fetch_via_http_scraper
from app.utils.local_scraper_client import fetch_via_local_scraper

logger = logging.getLogger(__name__)


async def fetch_allegro_data(ean: str, strategy: ScrapingStrategyConfig) -> AllegroResult:
    """Fetch Allegro data using configured strategy order."""
    logger.info(
        "SCRAPER_STRATEGY ean=%s use_cloud_http=%s use_local_scraper=%s",
        ean,
        strategy.use_cloud_http,
        strategy.use_local_scraper,
    )

    # 1. Cloud HTTP scraping
    if strategy.use_cloud_http:
        logger.info("SCRAPER_STEP cloud_http start ean=%s", ean)
        result = await fetch_via_http_scraper(ean)
        if not result.is_temporary_error:
            logger.info("SCRAPER_STEP cloud_http finish ean=%s source=%s", ean, result.source)
            return result

    # 2. Local Selenium scraper
    if (
        strategy.use_local_scraper
        and settings.LOCAL_SCRAPER_ENABLED
        and settings.LOCAL_SCRAPER_URL
    ):
        logger.info("SCRAPER_STEP local_scraper start ean=%s", ean)
        result = await fetch_via_local_scraper(ean)
        logger.info(
            "SCRAPER_STEP local_scraper finish ean=%s source=%s blocked=%s not_found=%s",
            ean,
            result.source,
            getattr(result, "blocked", False),
            result.is_not_found,
        )
        return result

    logger.warning("SCRAPER_STRATEGY fallback temporary_error ean=%s", ean)
    return AllegroResult(
        price=None,
        sold_count=None,
        is_not_found=False,
        is_temporary_error=True,
        raw_payload={"error": "all_strategies_failed_or_disabled", "ean": ean},
    )
