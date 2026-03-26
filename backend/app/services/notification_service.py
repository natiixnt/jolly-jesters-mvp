from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.notification import Notification

logger = logging.getLogger(__name__)


def create_notification(
    db: Session,
    tenant_id: str,
    notification_type: str,
    title: str,
    message: str,
    user_id: Optional[str] = None,
    channel: str = "in_app",
) -> Notification:
    n = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        message=message,
        channel=channel,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def list_notifications(
    db: Session,
    tenant_id: str,
    unread_only: bool = False,
    limit: int = 50,
) -> List[Notification]:
    q = db.query(Notification).filter(Notification.tenant_id == tenant_id)
    if unread_only:
        q = q.filter(Notification.is_read == False)
    return q.order_by(Notification.created_at.desc()).limit(limit).all()


def count_unread(db: Session, tenant_id: str) -> int:
    return (
        db.query(Notification)
        .filter(Notification.tenant_id == tenant_id, Notification.is_read == False)
        .count()
    )


def mark_read(db: Session, tenant_id: str, notification_id: int) -> bool:
    n = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.tenant_id == tenant_id)
        .first()
    )
    if not n:
        return False
    n.is_read = True
    n.read_at = datetime.now(timezone.utc)
    db.commit()
    return True


def mark_all_read(db: Session, tenant_id: str) -> int:
    count = (
        db.query(Notification)
        .filter(Notification.tenant_id == tenant_id, Notification.is_read == False)
        .update({"is_read": True, "read_at": datetime.now(timezone.utc)})
    )
    db.commit()
    return count
