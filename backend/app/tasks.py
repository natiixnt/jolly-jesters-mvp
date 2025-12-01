"""Compatibility entrypoint that forwards to the Celery app in app.workers.tasks."""

from app.workers.tasks import celery_app, run_analysis_task  # noqa: F401

celery = celery_app
