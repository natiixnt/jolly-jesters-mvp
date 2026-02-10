from datetime import datetime

from pydantic import BaseModel, Field


class SettingsRead(BaseModel):
    cache_ttl_days: int

    class Config:
        orm_mode = True


class SettingsUpdate(BaseModel):
    cache_ttl_days: int = Field(..., ge=0, le=365)


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
    updated_at: datetime | None = None
    sample: list[str] = []
    saved: bool | None = None
    reload: dict | None = None
    uploaded_at: datetime | None = None


class ProxyReloadResponse(BaseModel):
    status: str
    count: int | None = None
    path: str | None = None
