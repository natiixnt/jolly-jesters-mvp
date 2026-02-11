from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings
from app.models.category import Category
from app.models.enums import ProfitabilityLabel
from app.schemas.profitability import ProfitabilityDebug, ProfitabilityThresholds

REASON_PRIORITY = [
    "invalid_cost",
    "missing_data",
    "multiplier",
    "profit",
    "volume",
    "competition",
]


@dataclass
class ProfitabilityEvaluation:
    score: Decimal | None
    label: ProfitabilityLabel
    reason_code: str | None
    failed_thresholds: list[str]
    commission_rate: Decimal
    net_revenue: Decimal | None
    profit: Decimal | None
    multiplier: Decimal | None


def _pick_reason(failed_thresholds: list[str]) -> str | None:
    for key in REASON_PRIORITY:
        if key in failed_thresholds:
            return key
    return None


def evaluate_profitability(
    purchase_price: Decimal | None,
    allegro_price: Decimal | None,
    sold_count: int | None,
    category: Category,
    offer_count: int | None = None,
) -> ProfitabilityEvaluation:
    commission_rate = Decimal(category.commission_rate or 0)
    multiplier_threshold = Decimal(category.profitability_multiplier)
    min_profit_abs = Decimal(settings.profitability_min_profit_pln)
    min_sales = int(settings.profitability_min_sales)
    max_competition = int(settings.profitability_max_competition)

    failed: list[str] = []
    if purchase_price is None or purchase_price <= 0:
        failed.append("invalid_cost")
    if allegro_price is None:
        failed.append("missing_data")

    # Threshold checks only make sense with valid cost and price.
    net_revenue: Decimal | None = None
    profit: Decimal | None = None
    multiplier: Decimal | None = None
    if "invalid_cost" not in failed and "missing_data" not in failed:
        net_revenue = allegro_price * (Decimal("1") - commission_rate)
        profit = net_revenue - purchase_price
        multiplier = net_revenue / purchase_price

        if multiplier < multiplier_threshold:
            failed.append("multiplier")
        if profit < min_profit_abs:
            failed.append("profit")
        if sold_count is None or sold_count < min_sales:
            failed.append("volume")
        if offer_count is not None and offer_count > max_competition:
            failed.append("competition")

    reason_code = _pick_reason(failed)

    if any(key in failed for key in ("invalid_cost", "missing_data")):
        label = ProfitabilityLabel.nieokreslony
        score = None
    elif not failed:
        label = ProfitabilityLabel.oplacalny
        score = multiplier
    else:
        label = ProfitabilityLabel.nieoplacalny
        score = multiplier

    return ProfitabilityEvaluation(
        score=score,
        label=label,
        reason_code=reason_code,
        failed_thresholds=failed,
        commission_rate=commission_rate,
        net_revenue=net_revenue,
        profit=profit,
        multiplier=multiplier,
    )


def build_profitability_debug(
    purchase_price: Decimal | None,
    allegro_price: Decimal | None,
    sold_count: int | None,
    offer_count: int | None,
    category: Category | None,
    evaluation: ProfitabilityEvaluation | None = None,
) -> ProfitabilityDebug:
    version = "profitability_v2"
    multiplier_threshold = float(category.profitability_multiplier) if category else None

    thresholds = ProfitabilityThresholds(
        min_profit_pln=float(settings.profitability_min_profit_pln),
        min_sales=int(settings.profitability_min_sales),
        max_competition=int(settings.profitability_max_competition),
        multiplier_threshold=multiplier_threshold,
    )

    if evaluation is None and category is not None:
        evaluation = evaluate_profitability(
            purchase_price=purchase_price,
            allegro_price=allegro_price,
            sold_count=sold_count,
            category=category,
            offer_count=offer_count,
        )

    commission = (
        float(evaluation.commission_rate)
        if evaluation is not None
        else float(Decimal(category.commission_rate or 0) if category else Decimal("0"))
    )

    return ProfitabilityDebug(
        version=version,
        price_ref=float(allegro_price) if allegro_price is not None else None,
        commission=commission,
        net_revenue=float(evaluation.net_revenue) if evaluation and evaluation.net_revenue is not None else None,
        cost=float(purchase_price) if purchase_price is not None else None,
        profit=float(evaluation.profit) if evaluation and evaluation.profit is not None else None,
        multiplier=float(evaluation.multiplier) if evaluation and evaluation.multiplier is not None else None,
        sold_count=sold_count,
        offer_count_returned=offer_count,
        failed_thresholds=list(evaluation.failed_thresholds) if evaluation else [],
        thresholds=thresholds,
    )


def calculate_profitability(
    purchase_price: Decimal | None,
    allegro_price: Decimal | None,
    sold_count: int | None,
    category: Category,
    offer_count: int | None = None,
) -> tuple[Decimal | None, ProfitabilityLabel]:
    """Backward-compatible helper used by older call sites."""
    evaluation = evaluate_profitability(
        purchase_price=purchase_price,
        allegro_price=allegro_price,
        sold_count=sold_count,
        category=category,
        offer_count=offer_count,
    )
    return evaluation.score, evaluation.label
