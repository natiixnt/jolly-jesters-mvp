from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional


@dataclass
class AllegroResult:
    ean: str
    status: str
    total_offer_count: Optional[int]
    products: list[dict]
    price: Optional[Decimal]
    sold_count: Optional[int]
    is_not_found: bool
    is_temporary_error: bool
    raw_payload: Dict[str, Any]
    error: Optional[str] = None
    source: str = "allegro_scraper"
    scraped_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    captcha_solves: Optional[int] = None
