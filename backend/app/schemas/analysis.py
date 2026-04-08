from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, validator

from app.models.enums import AnalysisItemSource, AnalysisStatus, ProfitabilityLabel, ScrapeStatus
from app.schemas.category import CategoryRead
from app.schemas.profitability import ProfitabilityDebug


class AnalysisUploadResponse(BaseModel):
    analysis_run_id: int
    status: AnalysisStatus


class AnalysisStatusResponse(BaseModel):
    id: int
    category_id: UUID
    status: AnalysisStatus
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    total_products: int = Field(..., ge=0)
    processed_products: int = Field(..., ge=0)
    error_message: Optional[str] = Field(None, max_length=2000)
    run_metadata: Optional[dict] = None

    class Config:
        orm_mode = True


class AnalysisRunItemOut(BaseModel):
    id: int
    row_number: int = Field(..., ge=0)
    ean: str = Field(..., max_length=20)
    input_name: Optional[str] = Field(None, max_length=500)
    input_purchase_price: Optional[Decimal] = Field(None, ge=0)
    source: AnalysisItemSource
    allegro_price: Optional[Decimal] = Field(None, ge=0)
    allegro_sold_count: Optional[int] = Field(None, ge=0)
    profitability_score: Optional[Decimal] = None
    profitability_label: Optional[ProfitabilityLabel] = None
    error_message: Optional[str] = Field(None, max_length=2000)

    class Config:
        orm_mode = True


class AnalysisRunDetail(AnalysisStatusResponse):
    items: List[AnalysisRunItemOut]


class AnalysisRunSummary(BaseModel):
    id: int
    category_id: UUID
    category_name: Optional[str] = Field(None, max_length=255)
    created_at: Optional[datetime] = None
    status: AnalysisStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    total_products: int = Field(..., ge=0)
    processed_products: int = Field(..., ge=0)
    error_message: Optional[str] = Field(None, max_length=2000)

    class Config:
        orm_mode = True


class AnalysisRunListResponse(BaseModel):
    runs: List[AnalysisRunSummary]


class AnalysisResultItem(BaseModel):
    id: int
    row_number: Optional[int] = Field(None, ge=0)
    ean: str = Field(..., max_length=20)
    name: Optional[str] = Field(None, max_length=500)
    original_currency: Optional[str] = Field(None, max_length=10)
    original_purchase_price: Optional[float] = Field(None, ge=0)
    purchase_price_pln: Optional[float] = Field(None, ge=0)
    allegro_price_pln: Optional[float] = Field(None, ge=0)
    sold_count: Optional[int] = Field(None, ge=0)
    sold_count_status: Optional[str] = Field(None, max_length=50)
    margin_pln: Optional[float] = None
    margin_percent: Optional[float] = None
    is_profitable: Optional[bool] = None
    reason_code: Optional[str] = Field(None, max_length=100)
    source: Optional[str] = Field(None, max_length=50)
    scrape_status: Optional[ScrapeStatus] = None
    scrape_error_message: Optional[str] = Field(None, max_length=2000)
    last_checked_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    profitability_debug: Optional[ProfitabilityDebug] = None

    class Config:
        orm_mode = True


class AnalysisResultsResponse(BaseModel):
    run_id: int
    status: AnalysisStatus
    total: int = Field(..., ge=0)
    error_message: Optional[str] = Field(None, max_length=2000)
    items: List[AnalysisResultItem]
    next_since: Optional[datetime] = None
    next_since_id: Optional[int] = None


class AnalysisRunMetrics(BaseModel):
    run_id: int
    total_items: int = Field(..., ge=0)
    completed_items: int = Field(..., ge=0)
    failed_items: int = Field(..., ge=0)
    not_found_items: int = Field(..., ge=0)
    blocked_items: int = Field(..., ge=0)
    avg_latency_ms: Optional[float] = Field(None, ge=0)
    p50_latency_ms: Optional[float] = Field(None, ge=0)
    p95_latency_ms: Optional[float] = Field(None, ge=0)
    total_captcha_solves: int = Field(0, ge=0)
    total_retries: int = Field(0, ge=0)
    retry_rate: Optional[float] = Field(None, ge=0, le=1.0)
    captcha_rate: Optional[float] = Field(None, ge=0, le=1.0)
    blocked_rate: Optional[float] = Field(None, ge=0, le=1.0)
    network_error_rate: Optional[float] = Field(None, ge=0, le=1.0)
    ean_per_min: Optional[float] = Field(None, ge=0)
    cost_per_1000_ean: Optional[float] = Field(None, ge=0)
    elapsed_seconds: Optional[float] = Field(None, ge=0)
    success_rate: Optional[float] = Field(None, ge=0, le=1.0)


class AnalysisStartFromDbRequest(BaseModel):
    category_id: UUID
    cache_days: Optional[int] = Field(30, ge=0, le=3650)
    include_all_cached: bool = False
    only_with_data: bool = False
    limit: Optional[int] = Field(None, ge=1, le=100000)
    source: Optional[str] = Field(None, max_length=50)
    ean_contains: Optional[str] = Field(None, max_length=20, pattern=r'^[0-9]*$')

    class Config:
        extra = "ignore"
