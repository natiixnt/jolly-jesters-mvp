from io import BytesIO

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.analysis_run import AnalysisRun
from app.models.category import Category
from app.services.analysis_service import get_run_items, process_analysis_run
from app.services.import_service import prepare_analysis_run
from app.models.enums import ScrapeStatus
from app.services.schemas import ScrapingStrategyConfig
from app.utils.excel_reader import read_excel_file


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _excel_bytes():
    df = pd.DataFrame(
        [
            ["1234567890123", "Prod A", "100"],
            ["3213213213213", "Prod B", "200"],
        ],
        columns=["EAN", "Name", "PurchasePrice"],
    )
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()


def test_offline_analysis_flow(db_session):
    category = Category(
        name="TestCat",
        profitability_multiplier=0.2,
        commission_rate=0,
        is_active=True,
    )
    db_session.add(category)
    db_session.commit()
    db_session.refresh(category)

    rows = read_excel_file(_excel_bytes())
    strategy = ScrapingStrategyConfig(use_cloud_http=False, use_local_scraper=False)
    run = prepare_analysis_run(db_session, category, rows, "test.xlsx", strategy)

    process_analysis_run(db_session, run.id, mode="offline")

    refreshed_run = db_session.query(AnalysisRun).filter(AnalysisRun.id == run.id).first()
    assert refreshed_run.status.value == "completed"
    assert refreshed_run.processed_products == refreshed_run.total_products

    items = get_run_items(db_session, run.id)
    assert len(items) == len(rows)
    for item in items:
        assert item.profitability_label is not None
        assert item.scrape_status in {ScrapeStatus.ok, ScrapeStatus.not_found}
