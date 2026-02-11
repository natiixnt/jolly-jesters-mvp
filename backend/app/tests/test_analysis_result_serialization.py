from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, ProfitabilityLabel, ScrapeStatus
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.services.analysis_service import serialize_analysis_item


def _category() -> Category:
    return Category(
        name="Test",
        profitability_multiplier=Decimal("1.3"),
        commission_rate=Decimal("0.0"),
    )


def _item_with_offer_count(offer_count: int) -> AnalysisRunItem:
    category_id = uuid4()
    product = Product(
        category_id=category_id,
        ean="5901234123000",
        name="Example",
        purchase_price=Decimal("100"),
    )
    market_data = ProductMarketData(
        allegro_price=Decimal("150"),
        allegro_sold_count=10,
        is_not_found=False,
        raw_payload={"products": [{} for _ in range(offer_count)]},
        last_checked_at=datetime.now(timezone.utc),
    )
    state = ProductEffectiveState(
        product_id=product.id,
        last_checked_at=datetime.now(timezone.utc),
    )
    state.last_market_data = market_data
    product.effective_state = state

    return AnalysisRunItem(
        id=1,
        row_number=1,
        ean=product.ean,
        input_name="Example",
        original_purchase_price=Decimal("100"),
        original_currency="PLN",
        input_purchase_price=Decimal("100"),
        purchase_price_pln=Decimal("100"),
        source=AnalysisItemSource.baza,
        allegro_price=Decimal("150"),
        allegro_sold_count=10,
        profitability_score=Decimal("1.50"),
        profitability_label=ProfitabilityLabel.nieoplacalny,
        scrape_status=ScrapeStatus.ok,
        product=product,
    )


def test_serialize_analysis_item_sets_reason_code_and_debug_thresholds():
    item = _item_with_offer_count(offer_count=60)
    result = serialize_analysis_item(item, _category(), run_mode="live", include_debug=True)
    assert result.reason_code == "competition"
    assert result.source == "db_cache"
    assert result.profitability_debug is not None
    assert result.profitability_debug.failed_thresholds == ["competition"]


def test_serialize_analysis_item_maps_cached_mode_to_db_source():
    item = _item_with_offer_count(offer_count=10)
    result = serialize_analysis_item(item, _category(), run_mode="cached", include_debug=False)
    assert result.source == "db"
