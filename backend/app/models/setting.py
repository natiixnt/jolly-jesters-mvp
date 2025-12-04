from sqlalchemy import Column, Integer

from app.db.base import Base


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    cache_ttl_days = Column(Integer, nullable=False, default=30)
    local_scraper_windows = Column(Integer, nullable=False, default=1)
