from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ProfitabilityThresholds(BaseModel):
    min_profit_pln: float
    min_sales: int = Field(..., ge=0)
    max_competition: int = Field(..., ge=0)
    multiplier_threshold: Optional[float] = None


class ProfitabilityDebug(BaseModel):
    version: str = Field(..., max_length=50)
    price_ref: Optional[float] = None
    commission: Optional[float] = None
    net_revenue: Optional[float] = None
    cost: Optional[float] = None
    profit: Optional[float] = None
    multiplier: Optional[float] = None
    sold_count: Optional[int] = Field(None, ge=0)
    offer_count_returned: Optional[int] = Field(None, ge=0)
    failed_thresholds: list[str] = Field(default_factory=list)
    thresholds: ProfitabilityThresholds
