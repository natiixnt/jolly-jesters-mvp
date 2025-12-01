from decimal import Decimal

from app.models.category import Category
from app.models.enums import ProfitabilityLabel
from app.services.profitability_service import calculate_profitability


def test_profitable_case():
    category = Category(
        name="Test",
        profitability_multiplier=Decimal("0.2"),
        commission_rate=Decimal("0.05"),
    )
    score, label = calculate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("150"),
        sold_count=10,
        category=category,
    )
    assert score is not None
    assert score > 0
    assert label == ProfitabilityLabel.oplacalny


def test_non_profitable_case():
    category = Category(
        name="Test",
        profitability_multiplier=Decimal("0.5"),
        commission_rate=Decimal("0.05"),
    )
    score, label = calculate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("120"),
        sold_count=5,
        category=category,
    )
    assert label == ProfitabilityLabel.nieoplacalny


def test_no_allegro_price():
    category = Category(
        name="Test",
        profitability_multiplier=Decimal("0.5"),
        commission_rate=Decimal("0.05"),
    )
    score, label = calculate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=None,
        sold_count=None,
        category=category,
    )
    assert score is None
    assert label == ProfitabilityLabel.nieokreslony


def test_invalid_purchase_price():
    category = Category(
        name="Test",
        profitability_multiplier=Decimal("0.5"),
        commission_rate=Decimal("0.05"),
    )
    score, label = calculate_profitability(
        purchase_price=Decimal("0"),
        allegro_price=Decimal("200"),
        sold_count=None,
        category=category,
    )
    assert score is None
    assert label == ProfitabilityLabel.nieokreslony


def test_commission_and_multiplier_effect():
    category = Category(
        name="Test",
        profitability_multiplier=Decimal("0.3"),
        commission_rate=Decimal("0.1"),
    )
    score, label = calculate_profitability(
        purchase_price=Decimal("100"),
        allegro_price=Decimal("130"),
        sold_count=None,
        category=category,
    )
    assert score == Decimal("0.17")
    assert label == ProfitabilityLabel.nieoplacalny
