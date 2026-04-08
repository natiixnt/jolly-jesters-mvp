import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.schemas.settings import CurrencyRates, SettingsRead, SettingsUpdate
from app.services import settings_service
from app.services.audit_service import log_event

router = APIRouter(tags=["settings"])


@router.get("/", response_model=SettingsRead)
def get_settings(db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    record = settings_service.get_settings(db)
    if not record:
        raise HTTPException(status_code=404, detail="Nie znaleziono ustawien")
    return record


@router.put("/", response_model=SettingsRead)
def update_settings(request: Request, payload: SettingsUpdate, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    record = settings_service.update_settings(
        db=db,
        cache_ttl_days=payload.cache_ttl_days,
        stoploss_enabled=payload.stoploss_enabled,
        stoploss_window_size=payload.stoploss_window_size,
        stoploss_max_error_rate=payload.stoploss_max_error_rate,
        stoploss_max_captcha_rate=payload.stoploss_max_captcha_rate,
        stoploss_max_consecutive_errors=payload.stoploss_max_consecutive_errors,
    )
    log_event("settings_update",
              user_id=str(current_user.user_id) if current_user else None,
              tenant_id=str(current_user.tenant_id) if current_user else None,
              ip=request.client.host if request.client else None,
              details={"changes": payload.dict(exclude_unset=True)})
    return record


@router.get("/currencies", response_model=CurrencyRates)
def get_currency_rates(db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    rates = settings_service.get_currency_rates(db)
    return {
        "rates": [
            {"currency": r.currency, "rate_to_pln": float(r.rate_to_pln), "is_default": bool(r.is_default)}
            for r in rates
        ]
    }


@router.put("/currencies", response_model=CurrencyRates)
def update_currency_rates(request: Request, payload: CurrencyRates, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    try:
        rates = settings_service.update_currency_rates(db, [r.dict() for r in payload.rates])
    except ValueError as exc:
        logger.warning("Currency update validation error: %s", exc)
        raise HTTPException(status_code=400, detail="Nieprawidlowe dane walutowe")
    log_event("currency_rates_update",
              user_id=str(current_user.user_id) if current_user else None,
              tenant_id=str(current_user.tenant_id) if current_user else None,
              ip=request.client.host if request.client else None)
    return {
        "rates": [
            {"currency": r.currency, "rate_to_pln": float(r.rate_to_pln), "is_default": bool(r.is_default)}
            for r in rates
        ]
    }
