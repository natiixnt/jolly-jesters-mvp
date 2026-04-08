from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.models.category import Category
from app.models.product import Product
from app.models.product_market_data import ProductMarketData

router = APIRouter(tags=["price-history"])


class PricePoint(BaseModel):
    price: Optional[str]
    sold_count: Optional[int]
    is_not_found: bool
    fetched_at: str
    source: str


@router.get("/{ean}", response_model=List[PricePoint])
def get_price_history(
    ean: str = Path(..., min_length=1, max_length=20, pattern=r"^[0-9A-Za-z]+$"),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    query = (
        db.query(ProductMarketData)
        .join(Product, ProductMarketData.product_id == Product.id)
        .join(Category, Product.category_id == Category.id)
        .filter(Product.ean == ean)
    )
    if current_user:
        query = query.filter(Category.tenant_id == current_user.tenant_id)
    rows = (
        query
        .order_by(ProductMarketData.fetched_at.desc())
        .limit(limit)
        .all()
    )
    return [
        PricePoint(
            price=str(r.allegro_price) if r.allegro_price else None,
            sold_count=r.allegro_sold_count,
            is_not_found=r.is_not_found or False,
            fetched_at=r.fetched_at.isoformat() if r.fetched_at else "",
            source=r.source.value if r.source else "unknown",
        )
        for r in rows
    ]
