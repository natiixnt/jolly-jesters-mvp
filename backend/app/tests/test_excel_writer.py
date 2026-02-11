from decimal import Decimal
from io import BytesIO

import pandas as pd

from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, ProfitabilityLabel, ScrapeStatus
from app.utils.excel_writer import build_analysis_excel


def _category() -> Category:
    return Category(
        name="Test",
        profitability_multiplier=Decimal("1.3"),
        commission_rate=Decimal("0.10"),
    )


def test_export_contains_reason_column():
    profitable_item = AnalysisRunItem(
        id=1,
        row_number=1,
        ean="5901234123457",
        input_name="Prod A",
        original_purchase_price=Decimal("100"),
        original_currency="PLN",
        input_purchase_price=Decimal("100"),
        purchase_price_pln=Decimal("100"),
        source=AnalysisItemSource.scraping,
        allegro_price=Decimal("166.67"),
        allegro_sold_count=10,
        profitability_score=Decimal("1.50"),
        profitability_label=ProfitabilityLabel.oplacalny,
        scrape_status=ScrapeStatus.ok,
    )
    invalid_cost_item = AnalysisRunItem(
        id=2,
        row_number=2,
        ean="5901234123458",
        input_name="Prod B",
        original_purchase_price=Decimal("0"),
        original_currency="PLN",
        input_purchase_price=Decimal("0"),
        purchase_price_pln=Decimal("0"),
        source=AnalysisItemSource.scraping,
        allegro_price=Decimal("120"),
        allegro_sold_count=10,
        profitability_score=None,
        profitability_label=ProfitabilityLabel.nieokreslony,
        scrape_status=ScrapeStatus.ok,
    )

    content = build_analysis_excel([profitable_item, invalid_cost_item], _category(), run_mode="live")
    frame = pd.read_excel(BytesIO(content))

    assert "Powod" in frame.columns
    assert frame.iloc[0]["Powod"] == "ok"
    assert frame.iloc[1]["Powod"] == "invalid_cost"
