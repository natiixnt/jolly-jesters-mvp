import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.settings import CurrencyRates, SettingsRead, SettingsUpdate
from app.services import settings_service
from app.utils.local_scraper_client import update_local_scraper_windows

router = APIRouter(tags=["settings"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=SettingsRead)
def get_settings(db: Session = Depends(get_db)):
    record = settings_service.get_settings(db)
    if not record:
        raise HTTPException(status_code=404, detail="Settings not found")
    return record


@router.put("/", response_model=SettingsRead)
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    record = settings_service.update_settings(
        db=db,
        cache_ttl_days=payload.cache_ttl_days,
        local_scraper_windows=payload.local_scraper_windows,
    )
    update_result = update_local_scraper_windows(record.local_scraper_windows)
    if update_result.get("status") == "error":
        logger.warning(
            "LOCAL_SCRAPER_CONFIG_UPDATE failed url=%s error=%s",
            update_result.get("url"),
            update_result.get("error"),
        )
    return record


@router.get("/currencies", response_model=CurrencyRates)
def get_currency_rates(db: Session = Depends(get_db)):
    rates = settings_service.get_currency_rates(db)
    return {
        "rates": [
            {"currency": r.currency, "rate_to_pln": float(r.rate_to_pln), "is_default": bool(r.is_default)}
            for r in rates
        ]
    }


@router.put("/currencies", response_model=CurrencyRates)
def update_currency_rates(payload: CurrencyRates, db: Session = Depends(get_db)):
    try:
        rates = settings_service.update_currency_rates(db, [r.dict() for r in payload.rates])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "rates": [
            {"currency": r.currency, "rate_to_pln": float(r.rate_to_pln), "is_default": bool(r.is_default)}
            for r in rates
        ]
    }
