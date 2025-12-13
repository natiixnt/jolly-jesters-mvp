from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.market_data import MarketDataResponse
from app.services import market_data_service

router = APIRouter(tags=["market-data"])


@router.get("", response_model=MarketDataResponse)
def list_market_data(
    category_id: Optional[str] = None,
    ean: Optional[str] = None,
    source: Optional[str] = None,
    updated_since: Optional[datetime] = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    return market_data_service.list_market_data(
        db,
        category_id=category_id,
        ean=ean,
        source=source,
        updated_since=updated_since,
        offset=offset,
        limit=limit,
    )
