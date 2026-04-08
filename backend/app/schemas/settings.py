from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SettingsRead(BaseModel):
    cache_ttl_days: int
    stoploss_enabled: bool = True
    stoploss_window_size: int = 20
    stoploss_max_error_rate: float = 0.50
    stoploss_max_captcha_rate: float = 0.80
    stoploss_max_consecutive_errors: int = 10
    stoploss_max_retry_rate: float = 0.05
    stoploss_max_blocked_rate: float = 0.10
    stoploss_max_cost_per_1000: float = 10.0

    class Config:
        orm_mode = True


class SettingsUpdate(BaseModel):
    cache_ttl_days: int = Field(..., ge=0, le=365)
    stoploss_enabled: Optional[bool] = None
    stoploss_window_size: Optional[int] = Field(None, ge=5, le=1000)
    stoploss_max_error_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    stoploss_max_captcha_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    stoploss_max_consecutive_errors: Optional[int] = Field(None, ge=1, le=1000)
    stoploss_max_retry_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    stoploss_max_blocked_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    stoploss_max_cost_per_1000: Optional[float] = Field(None, ge=0.0, le=10000.0)


class CurrencyRateEntry(BaseModel):
    currency: str = Field(..., min_length=1, max_length=10, pattern=r'^[A-Z]{2,10}$')
    rate_to_pln: float = Field(..., gt=0, le=1000000)
    is_default: bool = False


class CurrencyRates(BaseModel):
    rates: list[CurrencyRateEntry]


class ProxyMeta(BaseModel):
    path: str = Field(..., max_length=500)
    count: int = Field(..., ge=0)
    size_bytes: int = Field(..., ge=0)
    updated_at: Optional[datetime] = None
    sample: list[str] = Field(default_factory=list, max_length=100)
    saved: Optional[bool] = None
    reload: Optional[dict] = None
    uploaded_at: Optional[datetime] = None


class ProxyReloadResponse(BaseModel):
    status: str
    count: Optional[int] = None
    path: Optional[str] = None


# -- Network proxy pool --

class NetworkProxyOut(BaseModel):
    id: int
    url: str = Field(..., max_length=2048)
    label: Optional[str] = Field(None, max_length=255)
    is_active: bool
    success_count: int = Field(..., ge=0)
    fail_count: int = Field(..., ge=0)
    health_score: float = Field(..., ge=0, le=1.0)
    quarantine_until: Optional[datetime] = None
    quarantine_reason: Optional[str] = Field(None, max_length=500)
    last_success_at: Optional[datetime] = None
    last_fail_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        orm_mode = True


class NetworkProxyHealthSummary(BaseModel):
    total: int = Field(..., ge=0)
    active: int = Field(..., ge=0)
    quarantined: int = Field(..., ge=0)
    available: int
    avg_health_score: Optional[float] = Field(None, ge=0, le=1.0)
    total_success: int = Field(..., ge=0)
    total_fail: int = Field(..., ge=0)
    success_rate: Optional[float] = Field(None, ge=0, le=1.0)


class NetworkProxyImportResult(BaseModel):
    imported: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    total_lines: int = Field(..., ge=0)


class NetworkProxyQuarantineRequest(BaseModel):
    duration_minutes: int = Field(15, ge=1, le=1440)
    reason: str = Field("manual", max_length=500)
