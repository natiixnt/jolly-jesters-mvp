import secrets

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _generate_key() -> str:
    return f"jj_{secrets.token_urlsafe(32)}"


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    name = Column(String(255), nullable=False)
    key_hash = Column(String(128), unique=True, nullable=False, index=True)
    key_prefix = Column(String(16), nullable=False)  # first 8 chars for display
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
