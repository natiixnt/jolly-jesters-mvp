from decimal import Decimal

from app.models.category import Category
from app.models.enums import ProfitabilityLabel
from app.services.profitability_service import (
    build_profitability_debug,
    calculate_profitability,
    evaluate_profitability,
)


def _category(multiplier: str = "1.3", commission: str = "0.10") -> Category:
    return Category(
        name="Test",
        profitability_multiplier=Decimal(multiplier),
        commission_rate=Decimal(commission),
    )


def test_profitable_case():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("166.67"),
        sold_count=10,
        category=_category(multiplier="1.5"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.oplacalny
    assert evaluation.reason_code is None
    assert evaluation.failed_thresholds == []
    assert evaluation.multiplier is not None
    assert evaluation.multiplier.quantize(Decimal("0.01")) == Decimal("1.50")


def test_reason_invalid_cost():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("0"),
        allegro_price=Decimal("200"),
        sold_count=10,
        category=_category(),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieokreslony
    assert evaluation.reason_code == "invalid_cost"
    assert evaluation.failed_thresholds == ["invalid_cost"]


def test_reason_missing_data():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=None,
        sold_count=10,
        category=_category(),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieokreslony
    assert evaluation.reason_code == "missing_data"
    assert evaluation.failed_thresholds == ["missing_data"]


def test_reason_multiplier():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("127.78"),
        sold_count=10,
        category=_category(multiplier="1.3", commission="0.10"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "multiplier"
    assert evaluation.failed_thresholds == ["multiplier"]


def test_reason_profit():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("40"),
        allegro_price=Decimal("53"),
        sold_count=10,
        category=_category(multiplier="1.3", commission="0.00"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "profit"
    assert evaluation.failed_thresholds == ["profit"]


def test_reason_volume():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("160"),
        sold_count=2,
        category=_category(multiplier="1.3", commission="0.10"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "volume"
    assert evaluation.failed_thresholds == ["volume"]


def test_reason_competition():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("160"),
        sold_count=10,
        category=_category(multiplier="1.3", commission="0.10"),
        offer_count=60,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "competition"
    assert evaluation.failed_thresholds == ["competition"]


def test_multi_fail_reason_priority_multiplier():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("120"),
        sold_count=1,
        category=_category(multiplier="1.3", commission="0.10"),
        offer_count=80,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "multiplier"
    assert evaluation.failed_thresholds == ["multiplier", "profit", "volume", "competition"]


def test_multi_fail_reason_priority_invalid_cost():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("0"),
        allegro_price=None,
        sold_count=None,
        category=_category(),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieokreslony
    assert evaluation.reason_code == "invalid_cost"
    assert evaluation.failed_thresholds == ["invalid_cost", "missing_data"]


def test_debug_contains_failed_thresholds():
    category = _category(multiplier="1.3", commission="0.10")
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("120"),
        sold_count=1,
        category=category,
        offer_count=80,
    )
    debug = build_profitability_debug(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("120"),
        sold_count=1,
        offer_count=80,
        category=category,
        evaluation=evaluation,
    )
    assert debug.version == "profitability_v2"
    assert debug.failed_thresholds == ["multiplier", "profit", "volume", "competition"]


def test_calculate_profitability_stays_backward_compatible():
    score, label = calculate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("166.67"),
        sold_count=10,
        category=_category(multiplier="1.5", commission="0.10"),
        offer_count=10,
    )
    assert label == ProfitabilityLabel.oplacalny
    assert score is not None
