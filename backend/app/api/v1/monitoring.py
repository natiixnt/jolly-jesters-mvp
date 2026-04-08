from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.services import monitoring_service

router = APIRouter(tags=["monitoring"])

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000000"


class WatchRequest(BaseModel):
    ean: str
    label: Optional[str] = None
    priority: int = 0
    refresh_interval_minutes: int = 60


class BulkWatchRequest(BaseModel):
    eans: List[str]
    refresh_interval_minutes: int = 60


class MonitoredEANOut(BaseModel):
    id: int
    ean: str
    label: Optional[str]
    is_active: bool
    priority: int
    refresh_interval_minutes: int
    last_scraped_at: Optional[str]
    next_scrape_at: Optional[str]
    created_at: str

    class Config:
        orm_mode = True
        json_encoders = {object: str}


@router.get("/", response_model=List[MonitoredEANOut])
def list_watched(
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    items = monitoring_service.list_watched(db, tenant_id=tenant_id, active_only=active_only)
    return [_to_out(i) for i in items]


@router.post("/watch", response_model=MonitoredEANOut)
def watch_ean(
    req: WatchRequest,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    m = monitoring_service.watch_ean(
        db,
        tenant_id=tenant_id,
        ean=req.ean,
        label=req.label,
        priority=req.priority,
        refresh_interval_minutes=req.refresh_interval_minutes,
    )
    return _to_out(m)


@router.post("/watch/bulk")
def bulk_watch(
    req: BulkWatchRequest,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    added = monitoring_service.bulk_watch(
        db,
        tenant_id=tenant_id,
        eans=req.eans,
        refresh_interval_minutes=req.refresh_interval_minutes,
    )
    return {"added": added, "total": len(req.eans)}


@router.post("/unwatch/{ean}")
def unwatch_ean(
    ean: str,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    ok = monitoring_service.unwatch_ean(db, tenant_id=tenant_id, ean=ean)
    if not ok:
        raise HTTPException(404, "EAN not found in watchlist")
    return {"status": "ok"}


def _to_out(m) -> dict:
    return {
        "id": m.id,
        "ean": m.ean,
        "label": m.label,
        "is_active": m.is_active,
        "priority": m.priority,
        "refresh_interval_minutes": m.refresh_interval_minutes,
        "last_scraped_at": m.last_scraped_at.isoformat() if m.last_scraped_at else None,
        "next_scrape_at": m.next_scrape_at.isoformat() if m.next_scrape_at else None,
        "created_at": m.created_at.isoformat() if m.created_at else "",
    }
