from __future__ import annotations

import logging

from app.core.config import settings
from app.services.schemas import AllegroResult, ScrapingStrategyConfig
from app.utils.local_scraper_client import fetch_via_local_scraper

logger = logging.getLogger(__name__)


async def fetch_allegro_data(ean: str, strategy: ScrapingStrategyConfig) -> AllegroResult:
    """Fetch Allegro data using configured strategy order."""
    logger.info(
        "SCRAPER_STRATEGY ean=%s use_local_scraper=%s",
        ean,
        strategy.use_local_scraper,
    )

    # Lokalny scraper Selenium
    if strategy.use_local_scraper and settings.LOCAL_SCRAPER_ENABLED and settings.LOCAL_SCRAPER_URL:
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
    elif strategy.use_local_scraper and not settings.LOCAL_SCRAPER_ENABLED:
        logger.warning(
            "Local scraper strategy requested for ean=%s but LOCAL_SCRAPER_ENABLED is false. "
            "Set LOCAL_SCRAPER_ENABLED=true and expose the host scraper on LOCAL_SCRAPER_URL.",
            ean,
        )

    logger.warning("SCRAPER_STRATEGY fallback temporary_error ean=%s", ean)
    return AllegroResult(
        price=None,
        sold_count=None,
        is_not_found=False,
        is_temporary_error=True,
        raw_payload={"error": "all_strategies_failed_or_disabled", "ean": ean},
    )
