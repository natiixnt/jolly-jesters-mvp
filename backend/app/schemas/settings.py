from pydantic import BaseModel, Field


class SettingsRead(BaseModel):
    cache_ttl_days: int
    local_scraper_windows: int


class SettingsUpdate(BaseModel):
    cache_ttl_days: int = Field(..., ge=1, le=365)
    local_scraper_windows: int = Field(..., ge=1, le=50)
