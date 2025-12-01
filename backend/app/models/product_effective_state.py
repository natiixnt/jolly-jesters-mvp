from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import ProfitabilityLabel


class ProductEffectiveState(Base):
    __tablename__ = "product_effective_state"

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), primary_key=True)
    last_market_data_id = Column(ForeignKey("product_market_data.id"), nullable=True)
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    is_not_found = Column(Boolean, nullable=False, default=False, server_default="false")
    is_stale = Column(Boolean, nullable=False, default=False, server_default="false")
    profitability_score = Column(Numeric(12, 4), nullable=True)
    profitability_label = Column(Enum(ProfitabilityLabel), nullable=True)
    last_analysis_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    product = relationship("Product", back_populates="effective_state")
    last_market_data = relationship("ProductMarketData")
