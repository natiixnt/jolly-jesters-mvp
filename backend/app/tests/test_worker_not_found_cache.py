from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, ScrapeStatus
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.services.schemas import AllegroResult
from app.workers import tasks


def _category() -> Category:
    return Category(
        name="Test",
        profitability_multiplier=Decimal("1.3"),
        commission_rate=Decimal("0"),
    )


def test_apply_scraped_result_persists_not_found_market_data(monkeypatch):
    captured: dict = {}

    def _persist_stub(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(tasks, "_persist_market_data", _persist_stub)
    monkeypatch.setattr(tasks, "_update_effective_state", lambda *args, **kwargs: None)

    now = datetime.now(timezone.utc)
    result = AllegroResult(
        ean="5901111111111",
        status="no_results",
        total_offer_count=0,
        products=[],
        price=None,
        sold_count=None,
        is_not_found=True,
        is_temporary_error=False,
        raw_payload={"status": "no_results", "products": []},
        error=None,
        source="allegro_scraper",
        scraped_at=now,
    )

    item = AnalysisRunItem(
        ean="5901111111111",
        input_purchase_price=Decimal("100"),
        purchase_price_pln=Decimal("100"),
        source=AnalysisItemSource.baza,
        scrape_status=ScrapeStatus.pending,
    )
    product = Product(
        category_id=uuid4(),
        ean="5901111111111",
        name="X",
        purchase_price=Decimal("100"),
    )
    product.effective_state = ProductEffectiveState(product_id=uuid4())

    tasks._apply_scraped_result(
        db=object(),
        item=item,
        product=product,
        category=_category(),
        result=result,
    )

    assert item.source == AnalysisItemSource.not_found
    assert item.scrape_status == ScrapeStatus.not_found
    assert captured["is_not_found"] is True
    assert captured["last_checked_at"] == now
