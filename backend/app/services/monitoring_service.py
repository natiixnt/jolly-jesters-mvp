from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.monitored_ean import MonitoredEAN

logger = logging.getLogger(__name__)


def watch_ean(
    db: Session,
    tenant_id: str,
    ean: str,
    label: Optional[str] = None,
    priority: int = 0,
    refresh_interval_minutes: int = 60,
) -> MonitoredEAN:
    existing = (
        db.query(MonitoredEAN)
        .filter(MonitoredEAN.tenant_id == tenant_id, MonitoredEAN.ean == ean)
        .first()
    )
    if existing:
        existing.is_active = True
        existing.label = label or existing.label
        existing.priority = priority
        existing.refresh_interval_minutes = refresh_interval_minutes
        existing.next_scrape_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    now = datetime.now(timezone.utc)
    m = MonitoredEAN(
        tenant_id=tenant_id,
        ean=ean,
        label=label,
        priority=priority,
        refresh_interval_minutes=refresh_interval_minutes,
        next_scrape_at=now,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def unwatch_ean(db: Session, tenant_id: str, ean: str) -> bool:
    m = (
        db.query(MonitoredEAN)
        .filter(MonitoredEAN.tenant_id == tenant_id, MonitoredEAN.ean == ean)
        .first()
    )
    if not m:
        return False
    m.is_active = False
    db.commit()
    return True


def list_watched(
    db: Session, tenant_id: str, active_only: bool = True
) -> List[MonitoredEAN]:
    q = db.query(MonitoredEAN).filter(MonitoredEAN.tenant_id == tenant_id)
    if active_only:
        q = q.filter(MonitoredEAN.is_active == True)
    return q.order_by(MonitoredEAN.priority.desc(), MonitoredEAN.created_at).all()


def get_due_eans(db: Session, limit: int = 100) -> List[MonitoredEAN]:
    """Get EANs that need re-scraping (next_scrape_at <= now)."""
    now = datetime.now(timezone.utc)
    return (
        db.query(MonitoredEAN)
        .filter(
            MonitoredEAN.is_active == True,
            MonitoredEAN.next_scrape_at <= now,
        )
        .order_by(MonitoredEAN.priority.desc(), MonitoredEAN.next_scrape_at)
        .limit(limit)
        .all()
    )


def mark_scraped(db: Session, monitored_ean: MonitoredEAN) -> None:
    now = datetime.now(timezone.utc)
    monitored_ean.last_scraped_at = now
    monitored_ean.next_scrape_at = now + timedelta(minutes=monitored_ean.refresh_interval_minutes)
    db.commit()


def count_watched(db: Session, tenant_id: str) -> int:
    return (
        db.query(MonitoredEAN)
        .filter(MonitoredEAN.tenant_id == tenant_id, MonitoredEAN.is_active == True)
        .count()
    )


def bulk_watch(
    db: Session,
    tenant_id: str,
    eans: List[str],
    refresh_interval_minutes: int = 60,
) -> int:
    """Watch multiple EANs at once. Returns count of newly added."""
    existing = set(
        row[0]
        for row in db.query(MonitoredEAN.ean)
        .filter(MonitoredEAN.tenant_id == tenant_id, MonitoredEAN.ean.in_(eans))
        .all()
    )
    now = datetime.now(timezone.utc)
    added = 0
    for ean in eans:
        if ean in existing:
            continue
        db.add(MonitoredEAN(
            tenant_id=tenant_id,
            ean=ean,
            refresh_interval_minutes=refresh_interval_minutes,
            next_scrape_at=now,
        ))
        added += 1
    if added:
        db.commit()
    return added
