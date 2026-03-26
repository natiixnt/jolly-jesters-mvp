from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import alert_engine

router = APIRouter(tags=["alerts"])

TENANT = "00000000-0000-0000-0000-000000000000"


class CreateRuleRequest(BaseModel):
    name: str
    condition_type: str  # price_below, price_above, price_drop_pct, out_of_stock
    threshold_value: Optional[Decimal] = None
    ean: Optional[str] = None
    category_id: Optional[str] = None
    notify_email: bool = True
    notify_webhook: bool = False
    webhook_url: Optional[str] = None


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
def list_rules(active_only: bool = True, db: Session = Depends(get_db)):
    rules = alert_engine.list_rules(db, tenant_id=TENANT, active_only=active_only)
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
def create_rule(req: CreateRuleRequest, db: Session = Depends(get_db)):
    r = alert_engine.create_rule(
        db,
        tenant_id=TENANT,
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
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    ok = alert_engine.delete_rule(db, tenant_id=TENANT, rule_id=rule_id)
    if not ok:
        raise HTTPException(404, "Rule not found")
    return {"status": "ok"}


@router.get("/events", response_model=List[AlertEventOut])
def list_events(limit: int = 50, db: Session = Depends(get_db)):
    events = alert_engine.list_events(db, tenant_id=TENANT, limit=limit)
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
