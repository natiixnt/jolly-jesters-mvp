from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.profitability import ProfitabilityDebug


class MarketDataItem(BaseModel):
    ean: str = Field(..., max_length=20)
    name: str = Field(..., max_length=500)
    category_name: str = Field(..., max_length=255)
    purchase_price_pln: Optional[float] = Field(None, ge=0)
    allegro_price_pln: Optional[float] = Field(None, ge=0)
    sold_count: Optional[int] = Field(None, ge=0)
    is_profitable: Optional[bool] = None
    reason_code: Optional[str] = Field(None, max_length=100)
    source: Optional[str] = Field(None, max_length=50)
    last_checked_at: Optional[datetime] = None
    last_run_id: Optional[int] = None
    last_run_at: Optional[datetime] = None
    profitability_debug: Optional[ProfitabilityDebug] = None


class MarketDataResponse(BaseModel):
    total: int = Field(..., ge=0)
    items: List[MarketDataItem]
