from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel

from app.models.enums import AnalysisItemSource, AnalysisStatus, ProfitabilityLabel


class AnalysisUploadResponse(BaseModel):
    analysis_run_id: int
    status: AnalysisStatus


class AnalysisStatusResponse(BaseModel):
    id: int
    category_id: str
    status: AnalysisStatus
    started_at: datetime
    finished_at: Optional[datetime]
    total_products: int
    processed_products: int
    error_message: Optional[str]

    class Config:
        orm_mode = True


class AnalysisRunItemOut(BaseModel):
    id: int
    row_number: int
    ean: str
    input_name: Optional[str]
    input_purchase_price: Decimal
    source: AnalysisItemSource
    allegro_price: Optional[Decimal]
    allegro_sold_count: Optional[int]
    profitability_score: Optional[Decimal]
    profitability_label: Optional[ProfitabilityLabel]
    error_message: Optional[str]

    class Config:
        orm_mode = True


class AnalysisRunDetail(AnalysisStatusResponse):
    items: List[AnalysisRunItemOut]
