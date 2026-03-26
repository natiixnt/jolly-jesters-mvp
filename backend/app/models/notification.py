from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    # type: alert, run_completed, run_stopped, quota_warning, system
    notification_type = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, nullable=False, server_default="false", default=False)

    # delivery
    channel = Column(String(32), nullable=False, server_default="in_app", default="in_app")  # in_app, email, webhook
    delivered = Column(Boolean, nullable=False, server_default="false", default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
