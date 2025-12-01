from sqlalchemy import Column, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import AnalysisItemSource, ProfitabilityLabel


class AnalysisRunItem(Base):
    __tablename__ = "analysis_run_items"

    id = Column(Integer, primary_key=True, index=True)
    analysis_run_id = Column(Integer, ForeignKey("analysis_runs.id"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True, index=True)
    row_number = Column(Integer, nullable=False)
    ean = Column(String(64), nullable=False)
    input_name = Column(Text, nullable=True)
    input_purchase_price = Column(Numeric(12, 4), nullable=True)
    source = Column(Enum(AnalysisItemSource), nullable=False)
    allegro_price = Column(Numeric(12, 4), nullable=True)
    allegro_sold_count = Column(Integer, nullable=True)
    profitability_score = Column(Numeric(12, 4), nullable=True)
    profitability_label = Column(Enum(ProfitabilityLabel), nullable=True)
    error_message = Column(Text, nullable=True)

    analysis_run = relationship("AnalysisRun", back_populates="items")
    product = relationship("Product", back_populates="analysis_items")
