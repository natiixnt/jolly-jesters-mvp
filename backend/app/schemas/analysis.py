from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, root_validator

from app.models.enums import AnalysisItemSource, AnalysisStatus, ProfitabilityLabel, ScrapeStatus
from app.schemas.category import CategoryRead


class AnalysisUploadResponse(BaseModel):
    analysis_run_id: int
    status: AnalysisStatus


class AnalysisStatusResponse(BaseModel):
    id: int
    category_id: int
    status: AnalysisStatus
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
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
    input_purchase_price: Optional[Decimal]
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


class AnalysisRunSummary(BaseModel):
    id: int
    category_id: int
    category_name: str
    created_at: Optional[datetime] = None
    status: AnalysisStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    total_products: int
    processed_products: int
    mode: Optional[str] = None
    use_cloud_http: bool
    use_local_scraper: bool
    error_message: Optional[str] = None

    class Config:
        orm_mode = True


class AnalysisRunListResponse(BaseModel):
    runs: List[AnalysisRunSummary]


class AnalysisResultItem(BaseModel):
    id: int
    row_number: Optional[int] = None
    ean: str
    name: Optional[str]
    original_currency: Optional[str]
    original_purchase_price: Optional[float]
    purchase_price_pln: Optional[float]
    allegro_price_pln: Optional[float]
    sold_count: Optional[int]
    margin_pln: Optional[float]
    margin_percent: Optional[float]
    is_profitable: Optional[bool]
    source: Optional[str]
    scrape_status: Optional[ScrapeStatus]
    scrape_error_message: Optional[str]
    last_checked_at: Optional[datetime]
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True


class AnalysisResultsResponse(BaseModel):
    run_id: int
    status: AnalysisStatus
    total: int
    error_message: Optional[str]
    items: List[AnalysisResultItem]
    next_since: Optional[datetime] = None
    next_since_id: Optional[int] = None


class AnalysisStartFromDbRequest(BaseModel):
    category_id: UUID
    mode: str = "mixed"
    use_cloud_http: bool = True
    use_local_scraper: bool = True
    cache_days: Optional[int] = 30
    include_all_cached: bool = False
    only_with_data: bool = False
    limit: Optional[int] = None
    source: Optional[str] = None
    ean_contains: Optional[str] = None

    @root_validator(pre=True)
    def _map_legacy_fields(cls, values):
        if values is None:
            return values
        if "cache_days" not in values:
            if "days_back" in values:
                values["cache_days"] = values.get("days_back")
            elif "last_days" in values:
                values["cache_days"] = values.get("last_days")
        if "only_with_data" not in values and "only_successful" in values:
            values["only_with_data"] = values.get("only_successful")
        return values

    class Config:
        extra = "ignore"


class AnalysisRetryResponse(BaseModel):
    run_id: int
    status: AnalysisStatus
    scheduled: int
