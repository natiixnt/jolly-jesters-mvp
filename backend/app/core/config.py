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

    data_root: Path = Field(default=Path("/workspace"), env="WORKSPACE")
    upload_dir_name: str = Field(default="uploads")
    export_dir_name: str = Field(default="exports")

    allegro_api_client_id: Optional[str] = Field(default=None, env="ALLEGRO_API_CLIENT_ID")
    allegro_api_client_secret: Optional[str] = Field(default=None, env="ALLEGRO_API_CLIENT_SECRET")
    allegro_api_token: Optional[str] = Field(default=None, env="ALLEGRO_API_TOKEN")
    proxy_list: List[str] = Field(default_factory=list, env="PROXY_LIST")
    proxy_timeout: float = Field(default=15.0)
    scraping_retries: int = Field(default=2)

    sqlalchemy_echo: bool = Field(default=False)

    class Config:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @validator("proxy_list", pre=True)
    def _split_proxy_list(cls, value):
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [item.strip() for item in str(value).split(",") if item.strip()]

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


settings = Settings()
