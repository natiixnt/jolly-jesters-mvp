from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, func, Index, desc
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import MarketDataSource


class ProductMarketData(Base):
    __tablename__ = "product_market_data"
    __table_args__ = (
        Index("ix_product_market_data_product_fetched", "product_id", desc("fetched_at")),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    allegro_price = Column(Numeric(12, 4), nullable=True)
    allegro_sold_count = Column(Integer, nullable=True)
    source = Column(Enum(MarketDataSource), nullable=False)
    last_checked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_not_found = Column(Boolean, nullable=False, default=False, server_default="false")
    raw_payload = Column(JSON, nullable=True)

    product = relationship("Product", back_populates="market_data")
