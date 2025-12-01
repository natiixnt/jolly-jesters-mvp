import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("category_id", "ean", name="uq_product_category_ean"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False, index=True)
    ean = Column(String(64), nullable=False, index=True)
    name = Column(Text, nullable=False)
    purchase_price = Column(Numeric(12, 4), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    category = relationship("Category", back_populates="products")
    market_data = relationship(
        "ProductMarketData",
        back_populates="product",
        order_by="desc(ProductMarketData.fetched_at)",
        cascade="all, delete-orphan",
    )
    effective_state = relationship(
        "ProductEffectiveState",
        back_populates="product",
        uselist=False,
        cascade="all, delete-orphan",
    )
    analysis_items = relationship("AnalysisRunItem", back_populates="product")
