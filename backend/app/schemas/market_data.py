from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class MarketDataItem(BaseModel):
    ean: str
    name: str
    category_name: str
    purchase_price_pln: Optional[float]
    allegro_price_pln: Optional[float]
    sold_count: Optional[int]
    is_profitable: Optional[bool]
    source: Optional[str]
    last_checked_at: Optional[datetime]
    last_run_id: Optional[int]
    last_run_at: Optional[datetime]


class MarketDataResponse(BaseModel):
    total: int
    items: List[MarketDataItem]
