from celery import Celery
from celery.signals import worker_process_init
import logging

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services.analysis_service import process_analysis_run
from app.utils.local_scraper_client import check_local_scraper_health

logger = logging.getLogger(__name__)

celery_app = Celery(
    "jolly-jesters",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
)

celery_app.conf.task_default_queue = "analysis"

celery = celery_app  # alias for CLI


@worker_process_init.connect
def _reset_db_connections(**kwargs):
    engine.dispose()
    if settings.LOCAL_SCRAPER_ENABLED and settings.LOCAL_SCRAPER_URL:
        try:
            payload = check_local_scraper_health(timeout=1.0)
            logger.info("Local scraper health ok (worker start): %s", payload)
        except Exception as exc:  # pragma: no cover - best effort log
            logger.warning("Local scraper health check failed (worker start): %s", exc)


@celery_app.task(acks_late=True)
def run_analysis_task(run_id: int, mode: str = "mixed"):
    db = SessionLocal()
    try:
        process_analysis_run(db, run_id=run_id, mode=mode)
    finally:
        db.close()
