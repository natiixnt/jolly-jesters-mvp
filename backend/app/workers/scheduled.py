"""Scheduled tasks - Celery Beat periodic jobs."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from app.core.celery_constants import ANALYSIS_QUEUE
from app.db.session import SessionLocal
from app.models.analysis_run import AnalysisRun
from app.models.category import Category
from app.models.enums import AnalysisStatus
from celery.schedules import crontab

from app.services.analysis_service import build_cached_worklist, prepare_cached_analysis_run, record_run_task
from app.services.proxy_pool_service import run_healthcheck
from app.workers.tasks import celery_app, run_analysis_task

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_DAYS = int(os.getenv("AUTO_REFRESH_DAYS", "7"))
REFRESH_LIMIT = int(os.getenv("AUTO_REFRESH_LIMIT", "500"))


@celery_app.task(name="scheduled.refresh_stale_products")
def refresh_stale_products():
    """Auto re-analyze products that haven't been checked in REFRESH_INTERVAL_DAYS."""
    if REFRESH_INTERVAL_DAYS <= 0:
        logger.info("SCHEDULED auto-refresh disabled (AUTO_REFRESH_DAYS=0)")
        return {"status": "disabled"}

    db = SessionLocal()
    try:
        categories = db.query(Category).filter(Category.is_active.is_(True)).all()
        total_queued = 0

        for category in categories:
            # check if there's already an active run for this category
            active = (
                db.query(AnalysisRun)
                .filter(
                    AnalysisRun.category_id == category.id,
                    AnalysisRun.status.in_([AnalysisStatus.running, AnalysisStatus.pending]),
                )
                .first()
            )
            if active:
                logger.info("SCHEDULED skip category=%s (active run exists)", category.name)
                continue

            products = build_cached_worklist(
                db,
                category_id=category.id,
                cache_days=REFRESH_INTERVAL_DAYS,
                include_all_cached=False,
                only_with_data=False,
                limit=REFRESH_LIMIT,
            )
            if not products:
                continue

            run_metadata = {
                "source": "scheduled_refresh",
                "cache_days": REFRESH_INTERVAL_DAYS,
                "limit": REFRESH_LIMIT,
            }
            run = prepare_cached_analysis_run(db, category, products, run_metadata=run_metadata)
            result = run_analysis_task.delay(run.id)
            run.root_task_id = result.id
            record_run_task(db, run, result.id, "scheduled_refresh")
            db.commit()
            total_queued += len(products)
            logger.info("SCHEDULED queued category=%s products=%d run_id=%d", category.name, len(products), run.id)

        return {"status": "ok", "queued": total_queued}
    except Exception:
        logger.exception("SCHEDULED refresh_stale_products failed")
        return {"status": "error"}
    finally:
        db.close()


@celery_app.task(name="proxy_healthcheck")
def proxy_healthcheck_task():
    """Periodic proxy pool healthcheck - recovers quarantined proxies."""
    db = SessionLocal()
    try:
        result = run_healthcheck(db)
        logger.info("Proxy healthcheck completed: %s", result)
        return result
    finally:
        db.close()


# Celery Beat schedule
celery_app.conf.beat_schedule = {
    "refresh-stale-products": {
        "task": "scheduled.refresh_stale_products",
        "schedule": 86400.0,  # every 24 hours
        "options": {"queue": ANALYSIS_QUEUE},
    },
    "proxy-healthcheck": {
        "task": "proxy_healthcheck",
        "schedule": crontab(minute="*/5"),
    },
}
celery_app.conf.timezone = "UTC"
