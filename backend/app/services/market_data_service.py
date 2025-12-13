from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import MarketDataSource
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.schemas.market_data import MarketDataItem, MarketDataResponse


def list_market_data(
    db: Session,
    category_id: Optional[str] = None,
    ean: Optional[str] = None,
    source: Optional[str] = None,
    updated_since: Optional[datetime] = None,
    offset: int = 0,
    limit: int = 50,
) -> MarketDataResponse:
    last_run_subq = (
        db.query(
            AnalysisRunItem.product_id.label("product_id"),
            func.max(AnalysisRunItem.analysis_run_id).label("last_run_id"),
        )
        .group_by(AnalysisRunItem.product_id)
        .subquery()
    )

    base = (
        db.query(
            Product,
            Category.name.label("category_name"),
            ProductEffectiveState,
            ProductMarketData,
            last_run_subq.c.last_run_id,
        )
        .join(Category, Product.category_id == Category.id)
        .outerjoin(ProductEffectiveState, ProductEffectiveState.product_id == Product.id)
        .outerjoin(ProductMarketData, ProductMarketData.id == ProductEffectiveState.last_market_data_id)
        .outerjoin(last_run_subq, last_run_subq.c.product_id == Product.id)
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
    for product, category_name, state, market_data, last_run_id in rows:
        items.append(
            MarketDataItem(
                ean=product.ean,
                name=product.name,
                category_name=category_name or "",
                purchase_price_pln=float(product.purchase_price) if product.purchase_price is not None else None,
                allegro_price_pln=float(market_data.allegro_price) if market_data and market_data.allegro_price is not None else None,
                sold_count=market_data.allegro_sold_count if market_data else None,
                source=getattr(market_data.source, "value", market_data.source) if market_data and market_data.source else None,
                last_checked_at=market_data.last_checked_at if market_data else None,
                last_run_id=last_run_id,
            )
        )

    return MarketDataResponse(total=total, items=items)
