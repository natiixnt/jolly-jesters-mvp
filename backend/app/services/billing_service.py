from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.enums import AnalysisStatus
from app.models.tenant import Tenant
from app.models.usage_record import UsageRecord

CAPTCHA_COST_USD = Decimal(os.getenv("CAPTCHA_COST_USD", "0.002"))


def record_run_usage(db: Session, run_id: int) -> Optional[UsageRecord]:
    """Record usage for a completed/stopped run. Called after run finishes."""
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run or not run.tenant_id:
        return None

    # count processed items and captchas
    ean_count = run.processed_products or 0
    captcha_count = (
        db.query(func.coalesce(func.sum(AnalysisRunItem.captcha_solves), 0))
        .filter(AnalysisRunItem.analysis_run_id == run_id)
        .scalar()
    ) or 0

    estimated_cost = Decimal(captcha_count) * CAPTCHA_COST_USD

    now = datetime.now(timezone.utc)
    period = now.strftime("%Y-%m")

    record = UsageRecord(
        tenant_id=run.tenant_id,
        user_id=run.user_id,
        analysis_run_id=run_id,
        period=period,
        ean_count=ean_count,
        captcha_count=int(captcha_count),
        estimated_cost=estimated_cost,
    )
    db.add(record)
    db.commit()
    return record


def get_period_usage(db: Session, tenant_id: UUID, period: Optional[str] = None) -> Dict:
    """Get usage summary for a tenant in a given period (default: current month)."""
    if not period:
        period = datetime.now(timezone.utc).strftime("%Y-%m")

    records = (
        db.query(UsageRecord)
        .filter(UsageRecord.tenant_id == tenant_id, UsageRecord.period == period)
        .all()
    )

    total_ean = sum(r.ean_count for r in records)
    total_captcha = sum(r.captcha_count for r in records)
    total_cost = sum(r.estimated_cost or Decimal(0) for r in records)
    run_count = len(records)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    quota = tenant.monthly_ean_quota if tenant else 0
    remaining = max(0, quota - total_ean)
    quota_used_pct = round(total_ean / quota * 100, 1) if quota > 0 else 0

    return {
        "tenant_id": str(tenant_id),
        "period": period,
        "total_ean": total_ean,
        "total_captcha": total_captcha,
        "total_cost_usd": round(float(total_cost), 4),
        "run_count": run_count,
        "quota": quota,
        "remaining": remaining,
        "quota_used_pct": quota_used_pct,
    }


def check_quota(db: Session, tenant_id: UUID, requested_ean: int = 0) -> Dict:
    """Check if tenant has enough quota for a new run."""
    usage = get_period_usage(db, tenant_id)
    remaining = usage["remaining"]
    allowed = remaining >= requested_ean if requested_ean > 0 else remaining > 0
    return {
        "allowed": allowed,
        "remaining": remaining,
        "requested": requested_ean,
        "quota": usage["quota"],
        "used": usage["total_ean"],
    }


def get_usage_history(db: Session, tenant_id: UUID, limit: int = 12) -> List[Dict]:
    """Get usage per period for the last N months."""
    rows = (
        db.query(
            UsageRecord.period,
            func.sum(UsageRecord.ean_count).label("total_ean"),
            func.sum(UsageRecord.captcha_count).label("total_captcha"),
            func.sum(UsageRecord.estimated_cost).label("total_cost"),
            func.count(UsageRecord.id).label("run_count"),
        )
        .filter(UsageRecord.tenant_id == tenant_id)
        .group_by(UsageRecord.period)
        .order_by(UsageRecord.period.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "period": r.period,
            "total_ean": int(r.total_ean or 0),
            "total_captcha": int(r.total_captcha or 0),
            "total_cost_usd": round(float(r.total_cost or 0), 4),
            "run_count": int(r.run_count or 0),
        }
        for r in rows
    ]
