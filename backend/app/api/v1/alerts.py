
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.services import alert_engine

router = APIRouter(tags=["alerts"])

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000000"


VALID_CONDITION_TYPES = {"price_below", "price_above", "price_drop_pct", "out_of_stock"}


class CreateRuleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    condition_type: str = Field(...)
    threshold_value: Optional[Decimal] = None
    ean: Optional[str] = Field(None, max_length=20, pattern=r"^[0-9A-Za-z]*$")
    category_id: Optional[str] = None
    notify_email: bool = True
    notify_webhook: bool = False
    webhook_url: Optional[str] = Field(None, max_length=2048)

    @validator("condition_type")
    def validate_condition_type(cls, v):
        if v not in VALID_CONDITION_TYPES:
            raise ValueError(f"condition_type must be one of: {', '.join(sorted(VALID_CONDITION_TYPES))}")
        return v


class AlertRuleOut(BaseModel):
    id: int
    name: str
    is_active: bool
    ean: Optional[str]
    condition_type: str
    threshold_value: Optional[str]
    last_triggered_at: Optional[str]
    created_at: str


class AlertEventOut(BaseModel):
    id: int
    ean: Optional[str]
    condition_type: str
    message: str
    details: Optional[dict]
    created_at: str


@router.get("/rules", response_model=List[AlertRuleOut])
def list_rules(
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    rules = alert_engine.list_rules(db, tenant_id=tenant_id, active_only=active_only)
    return [
        AlertRuleOut(
            id=r.id,
            name=r.name,
            is_active=r.is_active,
            ean=r.ean,
            condition_type=r.condition_type,
            threshold_value=str(r.threshold_value) if r.threshold_value else None,
            last_triggered_at=r.last_triggered_at.isoformat() if r.last_triggered_at else None,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rules
    ]


@router.post("/rules", response_model=AlertRuleOut)
def create_rule(
    req: CreateRuleRequest,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    r = alert_engine.create_rule(
        db,
        tenant_id=tenant_id,
        name=req.name,
        condition_type=req.condition_type,
        threshold_value=req.threshold_value,
        ean=req.ean,
        category_id=req.category_id,
        notify_email=req.notify_email,
        notify_webhook=req.notify_webhook,
        webhook_url=req.webhook_url,
    )
    return AlertRuleOut(
        id=r.id,
        name=r.name,
        is_active=r.is_active,
        ean=r.ean,
        condition_type=r.condition_type,
        threshold_value=str(r.threshold_value) if r.threshold_value else None,
        last_triggered_at=None,
        created_at=r.created_at.isoformat() if r.created_at else "",
    )


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    ok = alert_engine.delete_rule(db, tenant_id=tenant_id, rule_id=rule_id)
    if not ok:
        raise HTTPException(404, "Nie znaleziono reguly")
    return {"status": "ok"}


@router.get("/events", response_model=List[AlertEventOut])
def list_events(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    events = alert_engine.list_events(db, tenant_id=tenant_id, limit=limit)
    return [
        AlertEventOut(
            id=e.id,
            ean=e.ean,
            condition_type=e.condition_type,
            message=e.message,
            details=e.details,
            created_at=e.created_at.isoformat() if e.created_at else "",
        )
        for e in events
    ]
