from __future__ import annotations

from decimal import Decimal

from app.models.category import Category
from app.models.enums import ProfitabilityLabel


def calculate_profitability(
    purchase_price: Decimal,
    allegro_price: Decimal | None,
    sold_count: int | None,
    category: Category,
) -> tuple[Decimal | None, ProfitabilityLabel]:
    """Return profitability score and label based on category settings."""

    if allegro_price is None:
        return None, ProfitabilityLabel.nieokreslony

    commission_rate = Decimal(category.commission_rate or 0)
    multiplier = Decimal(category.profitability_multiplier)

    net_revenue = allegro_price * (Decimal("1") - commission_rate)
    margin = net_revenue - purchase_price

    if purchase_price is None or purchase_price == 0:
        return None, ProfitabilityLabel.nieokreslony

    score = margin / purchase_price
    if score >= multiplier:
        label = ProfitabilityLabel.oplacalny
    else:
        label = ProfitabilityLabel.nieoplacalny

    return score, label
