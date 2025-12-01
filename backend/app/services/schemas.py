from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional


@dataclass
class AllegroResult:
    price: Optional[Decimal]
    sold_count: Optional[int]
    is_not_found: bool
    is_temporary_error: bool
    raw_payload: Dict[str, Any]
    source: str = "scraping"


@dataclass
class ScrapingStrategyConfig:
    use_api: bool = True
    use_cloud_http: bool = True
    use_local_scraper: bool = True
