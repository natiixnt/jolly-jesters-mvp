from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional


@dataclass
class AllegroResult:
    price: Optional[Decimal]
    sold_count: Optional[int]
    is_not_found: bool
    is_temporary_error: bool
    raw_payload: Dict[str, Any]
    error: Optional[str] = None
    source: str = "scraping"
    last_checked_at: Optional[datetime] = None
    product_title: Optional[str] = None
    product_url: Optional[str] = None
    offers: Optional[list[dict]] = None
    blocked: bool = False
    fingerprint_id: Optional[str] = None


@dataclass
class ScrapingStrategyConfig:
    use_cloud_http: bool = False
    use_local_scraper: bool = True
