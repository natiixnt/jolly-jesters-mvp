from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.services.schemas import AllegroResult


class ScraperProvider(ABC):
    """Abstract interface for marketplace scraper providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'allegro_scraper')."""
        ...

    @abstractmethod
    async def fetch(self, ean: str, run_id: Optional[str] = None) -> AllegroResult:
        """Fetch product data by EAN. Returns normalised AllegroResult."""
        ...

    @abstractmethod
    def health(self) -> dict:
        """Synchronous health check. Returns dict with at least 'status' key."""
        ...
