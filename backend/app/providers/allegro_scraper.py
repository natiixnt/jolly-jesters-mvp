from __future__ import annotations

from typing import Optional

from app.providers.base import ScraperProvider
from app.services.schemas import AllegroResult
from app.utils.allegro_scraper_client import (
    check_scraper_health,
    fetch_via_allegro_scraper,
)


class AllegroScraperProvider(ScraperProvider):
    """Provider wrapping the allegro.pl-scraper-main Node.js service."""

    @property
    def name(self) -> str:
        return "allegro_scraper"

    async def fetch(self, ean: str, run_id: Optional[str] = None) -> AllegroResult:
        return await fetch_via_allegro_scraper(ean, run_id=run_id)

    def health(self) -> dict:
        return check_scraper_health()
