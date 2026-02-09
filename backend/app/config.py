# Backwards-compatibility shim; prefer importing from app.core.config.settings
from app.core.config import settings

DATABASE_URL = settings.db_url
CELERY_BROKER_URL = settings.celery_broker
CELERY_RESULT_BACKEND = settings.celery_backend
DEFAULT_CURRENCY = settings.default_currency
