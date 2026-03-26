from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)

    # what to watch
    ean = Column(String(64), nullable=True)  # null = all EANs
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)

    # condition: price_below, price_above, price_drop_pct, new_seller, out_of_stock
    condition_type = Column(String(64), nullable=False)
    threshold_value = Column(Numeric(12, 4), nullable=True)

    # delivery
    notify_email = Column(Boolean, nullable=False, server_default="true", default=True)
    notify_webhook = Column(Boolean, nullable=False, server_default="false", default=False)
    webhook_url = Column(Text, nullable=True)

    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
