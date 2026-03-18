from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import MarketDataSource, ProfitabilityLabel
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.schemas.market_data import MarketDataItem, MarketDataResponse
from app.services.profitability_service import build_profitability_debug, evaluate_profitability


def _resolve_source(market_data: ProductMarketData | None) -> Optional[str]:
    if not market_data:
        return None
    raw_source = None
    raw_payload = getattr(market_data, "raw_payload", None) or {}
    try:
        raw_source = raw_payload.get("source")
        if raw_payload.get("error"):
            return "error"
    except Exception:
        raw_source = None
    if raw_source:
        return raw_source
    if market_data.source:
        return getattr(market_data.source, "value", market_data.source)
    return None


def list_market_data(
    db: Session,
    category_id: Optional[str] = None,
    ean: Optional[str] = None,
    source: Optional[str] = None,
    updated_since: Optional[datetime] = None,
    with_data: bool = False,
    profitable_only: bool = False,
    include_debug: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> MarketDataResponse:
    last_run_subq = (
        db.query(
            AnalysisRunItem.product_id.label("product_id"),
            func.max(AnalysisRunItem.analysis_run_id).label("last_run_id"),
            func.max(AnalysisRun.created_at).label("last_run_at"),
        )
        .join(AnalysisRun, AnalysisRun.id == AnalysisRunItem.analysis_run_id)
        .group_by(AnalysisRunItem.product_id)
        .subquery()
    )

    latest_item_subq = (
        db.query(
            AnalysisRunItem.product_id.label("product_id"),
            func.max(AnalysisRunItem.id).label("item_id"),
        )
        .group_by(AnalysisRunItem.product_id)
        .subquery()
    )

    latest_item = aliased(AnalysisRunItem)

    base = (
        db.query(
            Product,
            Category,
            ProductEffectiveState,
            ProductMarketData,
            last_run_subq.c.last_run_id,
            last_run_subq.c.last_run_at,
            latest_item.input_name.label("latest_input_name"),
        )
        .join(Category, Product.category_id == Category.id)
        .outerjoin(ProductEffectiveState, ProductEffectiveState.product_id == Product.id)
        .outerjoin(ProductMarketData, ProductMarketData.id == ProductEffectiveState.last_market_data_id)
        .outerjoin(last_run_subq, last_run_subq.c.product_id == Product.id)
        .outerjoin(latest_item_subq, latest_item_subq.c.product_id == Product.id)
        .outerjoin(latest_item, latest_item.id == latest_item_subq.c.item_id)
    )

    if category_id:
        base = base.filter(Product.category_id == category_id)
    if ean:
        base = base.filter(Product.ean.ilike(f"%{ean}%"))
    if source:
        try:
            source_enum = MarketDataSource(source)
            base = base.filter(ProductMarketData.source == source_enum)
        except ValueError:
            pass
    if updated_since:
        base = base.filter(ProductMarketData.last_checked_at >= updated_since)
    if with_data:
        base = base.filter(
            ProductEffectiveState.last_market_data_id.isnot(None),
            ProductMarketData.id.isnot(None),
            ProductMarketData.is_not_found.is_(False),
            ProductMarketData.allegro_price.isnot(None),
        )
    if profitable_only:
        base = base.filter(ProductEffectiveState.profitability_label == ProfitabilityLabel.oplacalny)

    total = base.count()

    rows = (
        base.order_by(
            ProductMarketData.last_checked_at.desc().nullslast(),
            Product.created_at.desc(),
        )
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )

    items: List[MarketDataItem] = []
    for product, category, state, market_data, last_run_id, last_run_at, latest_input_name in rows:
        raw_title = None
        try:
            raw_title = (market_data.raw_payload or {}).get("product_title") if market_data else None
        except Exception:
            raw_title = None

        name = product.name
        if not name or name == product.ean:
            name = latest_input_name or raw_title or name or product.ean

        last_checked_at = None
        if market_data:
            last_checked_at = market_data.last_checked_at or market_data.fetched_at

        profitability_debug = None
        evaluation = None
        offer_count_returned = None
        if market_data:
            try:
                products_payload = (market_data.raw_payload or {}).get("products")
                if isinstance(products_payload, list):
                    offer_count_returned = len(products_payload)
            except Exception:
                offer_count_returned = None

        evaluation = evaluate_profitability(
            purchase_price=product.purchase_price,
            allegro_price=market_data.allegro_price if market_data else None,
            sold_count=market_data.allegro_sold_count if market_data else None,
            category=category,
            offer_count=offer_count_returned,
        )

        if include_debug:
            profitability_debug = build_profitability_debug(
                purchase_price=product.purchase_price,
                allegro_price=market_data.allegro_price if market_data else None,
                sold_count=market_data.allegro_sold_count if market_data else None,
                offer_count=offer_count_returned,
                category=category,
                evaluation=evaluation,
            )

        items.append(
            MarketDataItem(
                ean=product.ean,
                name=name or product.ean,
                category_name=category.name if category else "",
                purchase_price_pln=float(product.purchase_price) if product.purchase_price is not None else None,
                allegro_price_pln=float(market_data.allegro_price) if market_data and market_data.allegro_price is not None else None,
                sold_count=market_data.allegro_sold_count if market_data else None,
                is_profitable=(
                    state.profitability_label == ProfitabilityLabel.oplacalny
                    if (state and state.profitability_label and market_data and market_data.allegro_price is not None)
                    else None
                ),
                reason_code=evaluation.reason_code,
                source=_resolve_source(market_data),
                last_checked_at=last_checked_at,
                last_run_id=last_run_id,
                last_run_at=last_run_at,
                profitability_debug=profitability_debug,
            )
        )

    return MarketDataResponse(total=total, items=items)
