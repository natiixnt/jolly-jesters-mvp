from pydantic import BaseModel, Field


class SettingsRead(BaseModel):
    cache_ttl_days: int
    local_scraper_windows: int

    class Config:
        orm_mode = True


class SettingsUpdate(BaseModel):
    cache_ttl_days: int = Field(..., ge=1, le=365)
    local_scraper_windows: int = Field(..., ge=1, le=50)


class CurrencyRateEntry(BaseModel):
    currency: str
    rate_to_pln: float
    is_default: bool = False


class CurrencyRates(BaseModel):
    rates: list[CurrencyRateEntry]
