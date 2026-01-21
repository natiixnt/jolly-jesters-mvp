from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.analysis_run_task import AnalysisRunTask
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, MarketDataSource, ScrapeStatus
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.services.analysis_service import (
    build_cached_worklist,
    cancel_analysis_run,
    prepare_cached_analysis_run,
    record_run_task,
    retry_failed_items,
)
from app.services.schemas import ScrapingStrategyConfig


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


def _seed_category(db_session):
    category = Category(
        name="TestCat",
        profitability_multiplier=0.2,
        commission_rate=0,
        is_active=True,
    )
    db_session.add(category)
    db_session.commit()
    db_session.refresh(category)
    return category


def _seed_product_with_cache(db_session, category):
    product = Product(
        category_id=category.id,
        ean="1111111111111",
        name="Cached product",
        purchase_price=10,
    )
    db_session.add(product)
    db_session.flush()

    now = datetime.now(timezone.utc)
    market = ProductMarketData(
        product_id=product.id,
        allegro_price=20,
        allegro_sold_count=5,
        source=MarketDataSource.scraping,
        last_checked_at=now,
        fetched_at=now,
        is_not_found=False,
    )
    db_session.add(market)
    db_session.flush()

    state = ProductEffectiveState(
        product_id=product.id,
        last_market_data_id=market.id,
        last_checked_at=now,
        last_fetched_at=now,
        is_not_found=False,
    )
    db_session.add(state)
    db_session.commit()
    db_session.refresh(product)
    return product


def test_prepare_cached_analysis_run(db_session):
    category = _seed_category(db_session)
    _seed_product_with_cache(db_session, category)

    products = build_cached_worklist(db_session, category_id=category.id, cache_days=30)
    assert len(products) == 1

    strategy = ScrapingStrategyConfig(use_cloud_http=False, use_local_scraper=False)
    run = prepare_cached_analysis_run(db_session, category, products, strategy, mode="offline")

    assert run.id is not None
    assert run.total_products == 1
    assert run.status == AnalysisStatus.pending

    items = db_session.query(AnalysisRunItem).filter(AnalysisRunItem.analysis_run_id == run.id).all()
    assert len(items) == 1
    assert items[0].source == AnalysisItemSource.baza

    record_run_task(db_session, run, "task-1", "run_analysis")
    db_session.commit()
    assert db_session.query(AnalysisRunTask).count() == 1


def test_cancel_analysis_run(db_session):
    category = _seed_category(db_session)
    run = AnalysisRun(
        category_id=category.id,
        input_file_name="test.xlsx",
        status=AnalysisStatus.running,
        total_products=1,
        processed_products=0,
        mode="mixed",
        use_cloud_http=False,
        use_local_scraper=True,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    canceled = cancel_analysis_run(db_session, run.id)
    assert canceled is not None
    assert canceled.status == AnalysisStatus.canceled
    assert canceled.canceled_at is not None

    canceled_again = cancel_analysis_run(db_session, run.id)
    assert canceled_again is not None
    assert canceled_again.status == AnalysisStatus.canceled


def test_retry_failed_items(db_session):
    category = _seed_category(db_session)
    product = _seed_product_with_cache(db_session, category)

    run = AnalysisRun(
        category_id=category.id,
        input_file_name="test.xlsx",
        status=AnalysisStatus.failed,
        total_products=1,
        processed_products=1,
        mode="mixed",
        use_cloud_http=False,
        use_local_scraper=False,
    )
    db_session.add(run)
    db_session.flush()

    item = AnalysisRunItem(
        analysis_run_id=run.id,
        product_id=product.id,
        row_number=1,
        ean=product.ean,
        input_name=product.name,
        input_purchase_price=product.purchase_price,
        purchase_price_pln=product.purchase_price,
        source=AnalysisItemSource.error,
        scrape_status=ScrapeStatus.error,
        error_message="oops",
    )
    db_session.add(item)
    db_session.commit()

    def _enqueue(_item):
        return "task-retry-1"

    scheduled = retry_failed_items(db_session, run.id, enqueue=_enqueue)
    assert scheduled == 1

    refreshed = db_session.query(AnalysisRun).filter(AnalysisRun.id == run.id).first()
    assert refreshed.status == AnalysisStatus.running

    updated_item = db_session.query(AnalysisRunItem).filter(AnalysisRunItem.id == item.id).first()
    assert updated_item.scrape_status == ScrapeStatus.pending
    assert updated_item.error_message is None
    assert db_session.query(AnalysisRunTask).count() == 1
