import uuid

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(128), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)

    # billing
    plan = Column(String(64), nullable=False, server_default="free", default="free")
    monthly_ean_quota = Column(Integer, nullable=False, server_default="1000", default=1000)
    max_concurrent_runs = Column(Integer, nullable=False, server_default="3", default=3)

    # SLA / feature gates
    refresh_interval_minutes = Column(Integer, nullable=False, server_default="60", default=60)
    max_monitored_eans = Column(Integer, nullable=False, server_default="100", default=100)
    max_alert_rules = Column(Integer, nullable=False, server_default="10", default=10)
    api_access = Column(Boolean, nullable=False, server_default="false", default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    usage_records = relationship("UsageRecord", back_populates="tenant", cascade="all, delete-orphan")
