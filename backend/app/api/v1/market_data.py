from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
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
    with_data: bool = False,
    profitable_only: bool = False,
    debug: bool = False,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    result = market_data_service.list_market_data(
        db,
        category_id=category_id,
        ean=ean,
        source=source,
        updated_since=updated_since,
        with_data=with_data,
        profitable_only=profitable_only,
        include_debug=debug,
        offset=offset,
        limit=limit,
    )
    if not debug:
        payload = jsonable_encoder(
            result,
            exclude={"items": {"__all__": {"profitability_debug"}}},
        )
        return JSONResponse(content=payload)
    return result
