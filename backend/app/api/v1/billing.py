from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.services import billing_service

router = APIRouter(tags=["billing"])


class UsageSummary(BaseModel):
    tenant_id: str
    period: str
    total_ean: int
    total_captcha: int
    total_cost_usd: float
    run_count: int
    quota: int
    remaining: int
    quota_used_pct: float


class QuotaCheck(BaseModel):
    allowed: bool
    remaining: int
    requested: int
    quota: int
    used: int


class UsagePeriod(BaseModel):
    period: str
    total_ean: int
    total_captcha: int
    total_cost_usd: float
    run_count: int


@router.get("/usage", response_model=UsageSummary)
def get_usage(
    period: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Multi-tenant auth required")
    return billing_service.get_period_usage(db, current_user.tenant_id, period)


@router.get("/quota", response_model=QuotaCheck)
def check_quota(
    ean_count: int = 0,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Multi-tenant auth required")
    return billing_service.check_quota(db, current_user.tenant_id, ean_count)


@router.get("/usage/history", response_model=list[UsagePeriod])
def usage_history(
    limit: int = 12,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Multi-tenant auth required")
    return billing_service.get_usage_history(db, current_user.tenant_id, limit)
