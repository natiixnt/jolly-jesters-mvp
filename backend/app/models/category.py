import uuid

from sqlalchemy import Boolean, Column, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Category(Base):
    __tablename__ = "categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    profitability_multiplier = Column(Numeric(12, 4), nullable=False)
    commission_rate = Column(Numeric(12, 4), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    products = relationship("Product", back_populates="category", cascade="all, delete")
    analysis_runs = relationship("AnalysisRun", back_populates="category", cascade="all, delete")
