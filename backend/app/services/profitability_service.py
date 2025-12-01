from __future__ import annotations

from decimal import Decimal

from app.models.category import Category
from app.models.enums import ProfitabilityLabel


def calculate_profitability(
    purchase_price: Decimal | None,
    allegro_price: Decimal | None,
    sold_count: int | None,
    category: Category,
) -> tuple[Decimal | None, ProfitabilityLabel]:
    """Compute profitability based on category multiplier and commission."""

    if purchase_price is None or purchase_price <= 0:
        return None, ProfitabilityLabel.nieokreslony

    if allegro_price is None:
        return None, ProfitabilityLabel.nieokreslony

    commission_rate = Decimal(category.commission_rate or 0)
    multiplier = Decimal(category.profitability_multiplier)

    net_revenue = allegro_price * (Decimal("1") - commission_rate)
    margin = net_revenue - purchase_price
    score = margin / purchase_price

    if score >= multiplier:
        return score, ProfitabilityLabel.oplacalny
    return score, ProfitabilityLabel.nieoplacalny
