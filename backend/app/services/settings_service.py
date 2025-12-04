from sqlalchemy.orm import Session

from app.core.config import settings as app_config
from app.models.setting import Setting


def get_settings(db: Session) -> Setting:
    record = db.query(Setting).first()
    if record:
        return record

    record = Setting(
        cache_ttl_days=app_config.stale_days,
        local_scraper_windows=1,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_settings(db: Session, cache_ttl_days: int, local_scraper_windows: int) -> Setting:
    record = get_settings(db)
    record.cache_ttl_days = max(1, cache_ttl_days)
    record.local_scraper_windows = max(1, local_scraper_windows)
    db.commit()
    db.refresh(record)
    return record
