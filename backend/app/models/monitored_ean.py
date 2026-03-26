import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class MonitoredEAN(Base):
    __tablename__ = "monitored_eans"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    ean = Column(String(64), nullable=False, index=True)
    label = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)
    priority = Column(Integer, nullable=False, server_default="0", default=0)  # higher = scrape first
    refresh_interval_minutes = Column(Integer, nullable=False, server_default="60", default=60)
    last_scraped_at = Column(DateTime(timezone=True), nullable=True)
    next_scrape_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # one tenant can't watch same EAN twice
        {"sqlite_autoincrement": True},
    )
