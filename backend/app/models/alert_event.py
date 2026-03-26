from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import JSON

from app.db.base import Base


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    alert_rule_id = Column(Integer, ForeignKey("alert_rules.id"), nullable=False, index=True)
    ean = Column(String(64), nullable=True)
    condition_type = Column(String(64), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
