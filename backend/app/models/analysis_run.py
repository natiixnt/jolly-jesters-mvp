from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import AnalysisStatus


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False, index=True)
    input_file_name = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Enum(AnalysisStatus), nullable=False, default=AnalysisStatus.pending)
    error_message = Column(Text, nullable=True)
    total_products = Column(Integer, nullable=False, default=0)
    processed_products = Column(Integer, nullable=False, default=0)

    category = relationship("Category", back_populates="analysis_runs")
    items = relationship(
        "AnalysisRunItem",
        back_populates="analysis_run",
        cascade="all, delete-orphan",
    )
