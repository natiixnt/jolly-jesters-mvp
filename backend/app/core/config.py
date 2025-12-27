from __future__ import annotations
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from pydantic import BaseSettings, Field, validator


class Settings(BaseSettings):
    db_url: str = Field(
        default="postgresql+psycopg2://pilot:pilot@postgres:5432/pilotdb",
        env="DB_URL",
    )
    redis_url: str = Field(default="redis://redis:6379/0", env="REDIS_URL")
    celery_broker_url: Optional[str] = Field(default=None, env="CELERY_BROKER_URL")
    celery_result_backend: Optional[str] = Field(default=None, env="CELERY_RESULT_BACKEND")

    default_currency: str = Field(default="PLN")
    stale_days: int = Field(default=30)
    profitability_default_multiplier: Decimal = Field(default=Decimal("1.5"))
    commission_default: Optional[Decimal] = Field(default=None)
    eur_to_pln_rate: float = Field(default=4.5, env="EUR_TO_PLN_RATE")

    data_root: Path = Field(default=Path("/workspace"), env="WORKSPACE")
    upload_dir_name: str = Field(default="uploads")
    export_dir_name: str = Field(default="exports")

    allegro_api_client_id: Optional[str] = Field(default=None, env="ALLEGRO_API_CLIENT_ID")
    allegro_api_client_secret: Optional[str] = Field(default=None, env="ALLEGRO_API_CLIENT_SECRET")
    allegro_api_token: Optional[str] = Field(default=None, env="ALLEGRO_API_TOKEN")
    proxy_list_raw: Optional[str] = Field(default=None, env="PROXY_LIST")
    proxy_timeout: float = Field(default=15.0)
    local_scraper_timeout: float = Field(default=90.0, env="LOCAL_SCRAPER_TIMEOUT")
    scraping_retries: int = Field(default=2)
    local_scraper_enabled: bool = Field(default=False, env="LOCAL_SCRAPER_ENABLED")
    local_scraper_url: Optional[str] = Field(default=None, env="LOCAL_SCRAPER_URL")
    local_scraper_windows: int = Field(default=1, env="LOCAL_SCRAPER_WINDOWS")

    sqlalchemy_echo: bool = Field(default=False)

    class Config:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @validator("local_scraper_enabled", pre=True, always=True)
    def _coerce_local_scraper_enabled(cls, value: object) -> bool:
        """
        Allow missing/blank LOCAL_SCRAPER_ENABLED values to default to False.
        """
        if value is None:
            return False
        if isinstance(value, str):
            if not value.strip():
                return False
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def proxy_list(self) -> List[str]:
        if not self.proxy_list_raw:
            return []
        if isinstance(self.proxy_list_raw, list):
            return self.proxy_list_raw
        return [item.strip() for item in str(self.proxy_list_raw).split(",") if item.strip()]

    @property
    def upload_dir(self) -> Path:
        path = self.data_root / "data" / self.upload_dir_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def export_dir(self) -> Path:
        path = self.data_root / "data" / self.export_dir_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    # Friendly aliases mirroring env var names (used by some callers/tests)
    @property
    def ALLEGRO_API_TOKEN(self) -> Optional[str]:
        return self.allegro_api_token

    @property
    def PROXY_LIST(self) -> List[str]:
        return self.proxy_list

    @property
    def LOCAL_SCRAPER_ENABLED(self) -> bool:
        return bool(self.local_scraper_enabled)

    @property
    def LOCAL_SCRAPER_URL(self) -> Optional[str]:
        return self.local_scraper_url

    @property
    def LOCAL_SCRAPER_WINDOWS(self) -> int:
        try:
            return max(1, int(self.local_scraper_windows))
        except Exception:
            return 1


settings = Settings()
