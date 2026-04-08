from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.services import notification_service

router = APIRouter(tags=["notifications"])

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000000"


class NotificationOut(BaseModel):
    id: int
    notification_type: str
    title: str
    message: str
    is_read: bool
    channel: str
    created_at: str
    read_at: Optional[str]


@router.get("/", response_model=List[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    items = notification_service.list_notifications(db, tenant_id=tenant_id, unread_only=unread_only, limit=limit)
    return [
        NotificationOut(
            id=n.id,
            notification_type=n.notification_type,
            title=n.title,
            message=n.message,
            is_read=n.is_read,
            channel=n.channel,
            created_at=n.created_at.isoformat() if n.created_at else "",
            read_at=n.read_at.isoformat() if n.read_at else None,
        )
        for n in items
    ]


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    return {"count": notification_service.count_unread(db, tenant_id=tenant_id)}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    ok = notification_service.mark_read(db, tenant_id=tenant_id, notification_id=notification_id)
    if not ok:
        raise HTTPException(404, "Notification not found")
    return {"status": "ok"}


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    count = notification_service.mark_all_read(db, tenant_id=tenant_id)
    return {"marked": count}
