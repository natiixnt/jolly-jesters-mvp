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
    retries: Optional[int] = None
    attempts: Optional[int] = None
    proxy_url_hash: Optional[str] = None
    proxy_success: Optional[bool] = None
    # Robust fallback metadata
    strategy: Optional[str] = None  # 'raw' | 'stealthPlaywright' | 'antidetectBrowser' | 'mobileFallback'
    fallback_level: Optional[int] = None  # 0-3
    proxy_type: Optional[str] = None  # 'residential' | 'mobile' | 'sticky' | 'datacenter'
    antidetect_tool: Optional[str] = None  # 'kameleo' | 'camoufox' | 'octo' | 'gologin' | None
    session_id: Optional[str] = None  # sticky proxy session ID
    cost_breakdown: Optional[Dict[str, Any]] = None  # itemized costs
    total_cost_usd: Optional[float] = None  # precise per-task cost in USD
    browser_runtime_ms: Optional[int] = None  # browser runtime (0 for raw)
    attempted_levels: Optional[list] = None  # which fallback levels were tried
    level_errors: Optional[Dict[str, str]] = None  # errors per level
