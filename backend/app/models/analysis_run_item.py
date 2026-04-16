from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import AnalysisItemSource, ProfitabilityLabel, ScrapeStatus


class AnalysisRunItem(Base):
    __tablename__ = "analysis_run_items"

    id = Column(Integer, primary_key=True, index=True)
    analysis_run_id = Column(Integer, ForeignKey("analysis_runs.id"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True, index=True)
    row_number = Column(Integer, nullable=False)
    ean = Column(String(64), nullable=False)
    input_name = Column(Text, nullable=True)
    original_purchase_price = Column(Numeric(12, 4), nullable=True)
    original_currency = Column(String(8), nullable=True)
    input_purchase_price = Column(Numeric(12, 4), nullable=True)
    purchase_price_pln = Column(Numeric(12, 4), nullable=True)
    source = Column(Enum(AnalysisItemSource), nullable=False)
    allegro_price = Column(Numeric(12, 4), nullable=True)
    allegro_sold_count = Column(Integer, nullable=True)
    profitability_score = Column(Numeric(12, 4), nullable=True)
    profitability_label = Column(Enum(ProfitabilityLabel), nullable=True)
    error_message = Column(Text, nullable=True)
    scrape_status = Column(
        Enum(ScrapeStatus),
        nullable=False,
        server_default=ScrapeStatus.pending.value,
        default=ScrapeStatus.pending,
    )

    # -- metering fields --
    latency_ms = Column(Integer, nullable=True)
    captcha_solves = Column(Integer, nullable=True, default=0)
    retries = Column(Integer, nullable=True, default=0)
    attempts = Column(Integer, nullable=True, default=0)
    network_node_id = Column(String(64), nullable=True)
    provider_status = Column(String(32), nullable=True)

    # -- robust fallback strategy fields --
    strategy = Column(String(32), nullable=True)  # raw | stealthPlaywright | antidetectBrowser | mobileFallback
    fallback_level = Column(Integer, nullable=True)  # 0-3
    proxy_type = Column(String(16), nullable=True)  # residential | mobile | sticky | datacenter
    antidetect_tool = Column(String(16), nullable=True)  # kameleo | camoufox | octo | gologin
    session_id = Column(String(64), nullable=True)  # sticky proxy session ID
    cost_breakdown = Column(JSON, nullable=True)  # itemized cost details
    total_cost_usd = Column(Float, nullable=True)  # precise per-task cost in USD
    browser_runtime_ms = Column(Integer, nullable=True)  # browser runtime (0 for raw)

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    analysis_run = relationship("AnalysisRun", back_populates="items")
    product = relationship("Product", back_populates="analysis_items")
    tasks = relationship(
        "AnalysisRunTask",
        back_populates="analysis_run_item",
        cascade="all, delete-orphan",
    )
