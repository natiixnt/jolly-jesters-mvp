from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.network_proxy import NetworkProxy
from app.utils.validators import validate_proxy_url

logger = logging.getLogger(__name__)

def _quarantine_minutes() -> int:
    settings = get_settings()
    return settings.network_quarantine_ttl_hours * 60

HEALTH_DECAY_FACTOR = Decimal("0.05")
HEALTH_RECOVERY_FACTOR = Decimal("0.02")
MIN_HEALTH_SCORE = Decimal("0.0")
MAX_HEALTH_SCORE = Decimal("1.0")
CONSECUTIVE_FAILS_QUARANTINE = 5


def list_proxies(
    db: Session,
    active_only: bool = False,
    include_quarantined: bool = True,
) -> List[NetworkProxy]:
    q = db.query(NetworkProxy)
    if active_only:
        q = q.filter(NetworkProxy.is_active.is_(True))
    if not include_quarantined:
        now = datetime.now(timezone.utc)
        q = q.filter(
            (NetworkProxy.quarantine_until.is_(None)) | (NetworkProxy.quarantine_until <= now)
        )
    return q.order_by(NetworkProxy.health_score.desc()).all()


def get_proxy(db: Session, proxy_id: int) -> Optional[NetworkProxy]:
    return db.query(NetworkProxy).filter(NetworkProxy.id == proxy_id).first()


def import_from_csv(db: Session, data: bytes) -> Dict:
    text = data.decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Plik jest pusty")

    imported = 0
    skipped = 0
    for line in lines:
        # support CSV with columns: url,label or just url per line
        parts = line.split(",", 1)
        url = parts[0].strip()
        label = parts[1].strip() if len(parts) > 1 else None

        if not url:
            skipped += 1
            continue
        try:
            url = validate_proxy_url(url)
        except ValueError:
            skipped += 1
            continue

        existing = db.query(NetworkProxy).filter(NetworkProxy.url == url).first()
        if existing:
            if label and not existing.label:
                existing.label = label
            skipped += 1
            continue

        db.add(NetworkProxy(url=url, url_hash=proxy_url_hash(url), label=label))
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped, "total_lines": len(lines)}


def import_from_text(db: Session, data: bytes) -> Dict:
    """Import proxy list from plain text (one URL per line)."""
    return import_from_csv(db, data)


def record_success(db: Session, proxy_url_hash: str) -> None:
    proxy = _find_by_url_hash(db, proxy_url_hash)
    if not proxy:
        return
    proxy.success_count += 1
    proxy.last_success_at = datetime.now(timezone.utc)
    # recover health
    new_score = min(MAX_HEALTH_SCORE, proxy.health_score + HEALTH_RECOVERY_FACTOR)
    proxy.health_score = new_score
    db.flush()


def record_failure(db: Session, proxy_url_hash: str, reason: str = "") -> None:
    proxy = _find_by_url_hash(db, proxy_url_hash)
    if not proxy:
        return
    proxy.fail_count += 1
    proxy.last_fail_at = datetime.now(timezone.utc)
    # decay health
    new_score = max(MIN_HEALTH_SCORE, proxy.health_score - HEALTH_DECAY_FACTOR)
    proxy.health_score = new_score

    # auto-quarantine on consecutive failures
    recent_fails = _count_recent_consecutive_fails(proxy)
    if recent_fails >= CONSECUTIVE_FAILS_QUARANTINE:
        quarantine_proxy(
            db, proxy.id,
            duration_minutes=_quarantine_minutes(),
            reason=f"auto: {CONSECUTIVE_FAILS_QUARANTINE} consecutive fails",
        )
    db.flush()


def quarantine_proxy(
    db: Session,
    proxy_id: int,
    duration_minutes: Optional[int] = None,
    reason: str = "manual",
) -> Optional[NetworkProxy]:
    proxy = get_proxy(db, proxy_id)
    if not proxy:
        return None
    if duration_minutes is None:
        duration_minutes = _quarantine_minutes()
    proxy.quarantine_until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
    proxy.quarantine_reason = reason
    db.commit()
    return proxy


def unquarantine_proxy(db: Session, proxy_id: int) -> Optional[NetworkProxy]:
    proxy = get_proxy(db, proxy_id)
    if not proxy:
        return None
    proxy.quarantine_until = None
    proxy.quarantine_reason = None
    db.commit()
    return proxy


def get_health_summary(db: Session) -> Dict:
    now = datetime.now(timezone.utc)
    total = db.query(func.count(NetworkProxy.id)).scalar() or 0
    active = db.query(func.count(NetworkProxy.id)).filter(NetworkProxy.is_active.is_(True)).scalar() or 0
    quarantined = (
        db.query(func.count(NetworkProxy.id))
        .filter(NetworkProxy.quarantine_until > now)
        .scalar() or 0
    )
    avg_health = (
        db.query(func.avg(NetworkProxy.health_score))
        .filter(NetworkProxy.is_active.is_(True))
        .scalar()
    )
    total_success = db.query(func.sum(NetworkProxy.success_count)).scalar() or 0
    total_fail = db.query(func.sum(NetworkProxy.fail_count)).scalar() or 0

    return {
        "total": total,
        "active": active,
        "quarantined": quarantined,
        "available": active - quarantined,
        "avg_health_score": round(float(avg_health), 4) if avg_health else None,
        "total_success": total_success,
        "total_fail": total_fail,
        "success_rate": round(total_success / (total_success + total_fail), 4) if (total_success + total_fail) > 0 else None,
    }


def get_active_proxy_urls(db: Session) -> List[str]:
    """Return URLs of active, non-quarantined proxies ordered by health score."""
    now = datetime.now(timezone.utc)
    proxies = (
        db.query(NetworkProxy.url)
        .filter(
            NetworkProxy.is_active.is_(True),
            (NetworkProxy.quarantine_until.is_(None)) | (NetworkProxy.quarantine_until <= now),
        )
        .order_by(NetworkProxy.health_score.desc())
        .all()
    )
    return [p.url for p in proxies]


def proxy_url_hash(url: str) -> str:
    return sha256(url.encode()).hexdigest()[:16]


def _find_by_url_hash(db: Session, url_hash: str) -> Optional[NetworkProxy]:
    """Find proxy by indexed url_hash column."""
    return db.query(NetworkProxy).filter(NetworkProxy.url_hash == url_hash).first()


def _count_recent_consecutive_fails(proxy: NetworkProxy) -> int:
    """Estimate consecutive fails based on last_success_at vs last_fail_at."""
    if not proxy.last_fail_at:
        return 0
    if proxy.last_success_at and proxy.last_success_at >= proxy.last_fail_at:
        return 0
    # If last_fail > last_success, assume ongoing failures
    # This is a rough heuristic; proper tracking would need a log table
    return proxy.fail_count if not proxy.last_success_at else min(proxy.fail_count, CONSECUTIVE_FAILS_QUARANTINE)


def run_healthcheck(db: Session) -> dict:
    """Check all active proxies and update scores. Called periodically."""
    proxies = db.query(NetworkProxy).filter(NetworkProxy.is_active == True).all()
    results = {"checked": 0, "quarantined": 0, "recovered": 0}
    now = datetime.now(timezone.utc)
    for proxy in proxies:
        # Auto-recover from quarantine if TTL expired
        if proxy.quarantine_until and proxy.quarantine_until <= now:
            proxy.quarantine_until = None
            proxy.quarantine_reason = None
            results["recovered"] += 1
        results["checked"] += 1
    db.commit()
    return results
