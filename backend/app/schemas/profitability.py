from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ProfitabilityThresholds(BaseModel):
    min_profit_pln: float
    min_sales: int
    max_competition: int
    multiplier_threshold: Optional[float]


class ProfitabilityDebug(BaseModel):
    version: str
    price_ref: Optional[float]
    commission: Optional[float]
    net_revenue: Optional[float]
    cost: Optional[float]
    profit: Optional[float]
    multiplier: Optional[float]
    sold_count: Optional[int]
    offer_count_returned: Optional[int]
    failed_thresholds: list[str]
    thresholds: ProfitabilityThresholds
