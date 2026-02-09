from __future__ import annotations
from decimal import Decimal
from pathlib import Path
from typing import Optional

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    db_url: str = Field(
        default="postgresql+psycopg2://mvp:mvp@postgres:5432/mvpdb",
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
    scraper_proxies_file: str = Field(default="/workspace/data/proxies.txt", env="SCRAPER_PROXIES_FILE")

    # Single scraper service (allegro.pl-scraper-main)
    allegro_scraper_url: str = Field(default="http://allegro_scraper:3000", env="ALLEGRO_SCRAPER_URL")
    allegro_scraper_poll_interval: float = Field(default=1.0, env="ALLEGRO_SCRAPER_POLL_INTERVAL")
    allegro_scraper_timeout_seconds: float = Field(default=90.0, env="ALLEGRO_SCRAPER_TIMEOUT_SECONDS")

    sqlalchemy_echo: bool = Field(default=False)

    ui_basic_auth_user: str = Field(default="admin", env="UI_BASIC_AUTH_USER")
    ui_basic_auth_password: str = Field(default="1234", env="UI_BASIC_AUTH_PASSWORD")
    ui_password: str = Field(default="1234", env="UI_PASSWORD")
    ui_session_ttl_hours: int = Field(default=24, env="UI_SESSION_TTL_HOURS")

    class Config:
        env_file = Path(__file__).resolve().parents[2] / ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

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
    def proxies_file(self) -> Path:
        path = Path(self.scraper_proxies_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


settings = Settings()
