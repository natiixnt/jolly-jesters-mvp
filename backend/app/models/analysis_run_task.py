from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class AnalysisRunTask(Base):
    __tablename__ = "analysis_run_tasks"

    id = Column(Integer, primary_key=True, index=True)
    analysis_run_id = Column(Integer, ForeignKey("analysis_runs.id"), nullable=False, index=True)
    analysis_run_item_id = Column(Integer, ForeignKey("analysis_run_items.id"), nullable=True, index=True)
    celery_task_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    ean = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    analysis_run = relationship("AnalysisRun", back_populates="tasks")
    analysis_run_item = relationship("AnalysisRunItem", back_populates="tasks")
