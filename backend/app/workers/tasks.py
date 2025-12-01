from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services.analysis_service import process_analysis_run

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


@celery_app.task(acks_late=True)
def run_analysis_task(run_id: int, mode: str = "mixed"):
    db = SessionLocal()
    try:
        process_analysis_run(db, run_id=run_id, mode=mode)
    finally:
        db.close()
