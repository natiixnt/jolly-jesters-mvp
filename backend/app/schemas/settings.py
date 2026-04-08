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
    currency: str
    rate_to_pln: float
    is_default: bool = False


class CurrencyRates(BaseModel):
    rates: list[CurrencyRateEntry]


class ProxyMeta(BaseModel):
    path: str
    count: int
    size_bytes: int
    updated_at: Optional[datetime] = None
    sample: list[str] = []
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
    url: str
    label: Optional[str] = None
    is_active: bool
    success_count: int
    fail_count: int
    health_score: float
    quarantine_until: Optional[datetime] = None
    quarantine_reason: Optional[str] = None
    last_success_at: Optional[datetime] = None
    last_fail_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        orm_mode = True


class NetworkProxyHealthSummary(BaseModel):
    total: int
    active: int
    quarantined: int
    available: int
    avg_health_score: Optional[float] = None
    total_success: int
    total_fail: int
    success_rate: Optional[float] = None


class NetworkProxyImportResult(BaseModel):
    imported: int
    skipped: int
    total_lines: int


class NetworkProxyQuarantineRequest(BaseModel):
    duration_minutes: int = Field(15, ge=1, le=1440)
    reason: str = "manual"
