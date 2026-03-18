from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class NetworkProxy(Base):
    __tablename__ = "network_proxies"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(Text, nullable=False, unique=True)
    label = Column(String(128), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)

    # -- scoring --
    success_count = Column(Integer, nullable=False, server_default="0", default=0)
    fail_count = Column(Integer, nullable=False, server_default="0", default=0)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_fail_at = Column(DateTime(timezone=True), nullable=True)
    health_score = Column(Numeric(5, 4), nullable=False, server_default="1.0000", default=1.0)

    # -- quarantine --
    quarantine_until = Column(DateTime(timezone=True), nullable=True)
    quarantine_reason = Column(String(255), nullable=True)

    # -- timestamps --
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
