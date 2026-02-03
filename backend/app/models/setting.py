import sqlalchemy as sa
from sqlalchemy import Column, Integer

from app.db.base import Base


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    cache_ttl_days = Column(Integer, nullable=False, default=30)
    local_scraper_windows = Column(Integer, nullable=False, default=1)
    cloud_scraper_disabled = Column(
        sa.Boolean().with_variant(sa.Boolean(), "postgresql"),
        nullable=False,
        server_default="true",
        default=True,
    )
