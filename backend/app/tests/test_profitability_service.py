from decimal import Decimal

from app.models.category import Category
from app.models.enums import ProfitabilityLabel
from app.services.profitability_service import (
    build_profitability_debug,
    calculate_profitability,
    evaluate_profitability,
)


def _category(multiplier: str = "1.3", commission: str = "0.10", vat: str = "0.23") -> Category:
    cat = Category(
        name="Test",
        profitability_multiplier=Decimal(multiplier),
        commission_rate=Decimal(commission),
    )
    cat.vat_rate = Decimal(vat)
    return cat


# Reference example from Mateusz:
# purchase 50 EUR x 4.20 = 210 PLN, sale 400 brutto / 1.23 = 325.20 net,
# commission 12% x 400 = 48, delivery 5 -> profit = 62.20, multiplier = 325.20/210 = 1.549
def test_profitable_case():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("50"),
        allegro_price=Decimal("400"),
        sold_count=10,
        category=_category(multiplier="1.5", commission="0.12", vat="0.23"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.oplacalny
    assert evaluation.reason_code is None
    assert evaluation.failed_thresholds == []
    assert evaluation.profit is not None
    assert evaluation.profit.quantize(Decimal("0.01")) == Decimal("62.20")
    assert evaluation.multiplier.quantize(Decimal("0.001")) == Decimal("1.549")


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


# multiplier = 325.20/(50*4.20) = 1.549, threshold 2.0 -> nieoplacalny via multiplier
def test_reason_multiplier():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("50"),
        allegro_price=Decimal("400"),
        sold_count=10,
        category=_category(multiplier="2.0", commission="0.12", vat="0.23"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "multiplier"
    assert "multiplier" in evaluation.failed_thresholds


# Profit barely above 0 but below min 15: purchase 100 EUR x 4.20 = 420,
# sale 540 brutto / 1.23 = 439.02, commission 0 -> profit = 439.02 - 420 - 0 - 5 = 14.02
def test_reason_profit():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("540"),
        sold_count=10,
        category=_category(multiplier="1.0", commission="0.00", vat="0.23"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "profit"
    assert "profit" in evaluation.failed_thresholds


def test_reason_volume():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("50"),
        allegro_price=Decimal("400"),
        sold_count=2,
        category=_category(multiplier="1.5", commission="0.12", vat="0.23"),
        offer_count=10,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "volume"
    assert "volume" in evaluation.failed_thresholds


def test_reason_competition():
    evaluation = evaluate_profitability(
        purchase_price=Decimal("50"),
        allegro_price=Decimal("400"),
        sold_count=10,
        category=_category(multiplier="1.5", commission="0.12", vat="0.23"),
        offer_count=60,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "competition"
    assert "competition" in evaluation.failed_thresholds


def test_multi_fail_reason_priority_multiplier():
    # Way below threshold on multiple checks -> priority is multiplier
    evaluation = evaluate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("120"),
        sold_count=1,
        category=_category(multiplier="1.3", commission="0.10", vat="0.23"),
        offer_count=80,
    )
    assert evaluation.label == ProfitabilityLabel.nieoplacalny
    assert evaluation.reason_code == "multiplier"
    assert set(evaluation.failed_thresholds) >= {"multiplier", "profit", "volume", "competition"}


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
    assert set(evaluation.failed_thresholds) == {"invalid_cost", "missing_data"}


def test_debug_contains_failed_thresholds():
    category = _category(multiplier="1.3", commission="0.10", vat="0.23")
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
    assert "multiplier" in debug.failed_thresholds


def test_calculate_profitability_stays_backward_compatible():
    score, label = calculate_profitability(
        purchase_price=Decimal("50"),
        allegro_price=Decimal("400"),
        sold_count=10,
        category=_category(multiplier="1.5", commission="0.12", vat="0.23"),
        offer_count=10,
    )
    assert label == ProfitabilityLabel.oplacalny
    assert score is not None


# VAT 8% lowers the bar (less VAT to subtract), more sale stays as net
def test_vat_8_percent_higher_net():
    eval_23 = evaluate_profitability(
        purchase_price=Decimal("50"),
        allegro_price=Decimal("400"),
        sold_count=10,
        category=_category(multiplier="1.0", commission="0.10", vat="0.23"),
        offer_count=10,
    )
    eval_8 = evaluate_profitability(
        purchase_price=Decimal("50"),
        allegro_price=Decimal("400"),
        sold_count=10,
        category=_category(multiplier="1.0", commission="0.10", vat="0.08"),
        offer_count=10,
    )
    assert eval_8.profit > eval_23.profit
    assert eval_8.net_revenue > eval_23.net_revenue
