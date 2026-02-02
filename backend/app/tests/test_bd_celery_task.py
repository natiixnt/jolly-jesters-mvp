import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisStatus, AnalysisItemSource, ScrapeStatus, MarketDataSource
from app.models.product import Product
from app.services.schemas import AllegroResult
from app.workers import tasks


@pytest.fixture
def sqlite_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    monkeypatch.setattr(tasks, "SessionLocal", Session)
    yield Session
    engine.dispose()


def test_scrape_one_local_bd_unlocker_persists_result(sqlite_session, monkeypatch):
    os.environ["SCRAPER_MODE"] = "bd_unlocker"

    async def fake_bd_fetch(ean: str):
        return AllegroResult(
            price=Decimal("15.00"),
            sold_count=3,
            is_not_found=False,
            is_temporary_error=False,
            raw_payload={"provider": "bd_unlocker", "sold_count_status": "ok", "lowest_price_offer_id": "offerX"},
            source="cloud_http",
        )

    monkeypatch.setattr(tasks, "fetch_via_bd_unlocker", lambda ean: fake_bd_fetch(ean))

    session = sqlite_session()
    category = Category(name="Cat", profitability_multiplier=0.2, commission_rate=0, is_active=True)
    product = Product(ean="123", name="Prod", category=category, purchase_price=Decimal("0"))
    session.add_all([category, product])
    session.commit()
    session.refresh(category)
    session.refresh(product)

    run = AnalysisRun(
        category_id=category.id,
        input_file_name="t.xlsx",
        input_source="test",
        status=AnalysisStatus.running,
        total_products=1,
        processed_products=0,
        mode="mixed",
        use_cloud_http=False,
        use_local_scraper=True,
    )
    item = AnalysisRunItem(
        analysis_run=run,
        product=product,
        row_number=1,
        ean=product.ean,
        input_name="Prod",
        source=AnalysisItemSource.scraping,
    )
    session.add_all([run, item])
    session.commit()
    session.refresh(run)
    session.refresh(item)
    ean_value = product.ean
    session.close()  # tasks.SessionLocal will open new sessions

    tasks.scrape_one_local.run(ean_value, item.id, {})

    session = sqlite_session()
    refreshed = session.query(AnalysisRunItem).filter(AnalysisRunItem.id == item.id).first()
    assert refreshed.allegro_price == Decimal("15.00")
    assert refreshed.allegro_sold_count == 3
    assert refreshed.scrape_status == ScrapeStatus.ok
    assert refreshed.source == AnalysisItemSource.scraping

    md = refreshed.product.effective_state.last_market_data
    assert md is not None
    assert md.source == MarketDataSource.cloud_http
    assert (md.raw_payload or {}).get("provider") == "bd_unlocker"
    assert (md.raw_payload or {}).get("sold_count_status") == "ok"
