# Compatibility wrapper around the new settings module.
# Prefer importing from app.core.config directly in new code.
from app.core.config import settings

DATABASE_URL = settings.db_url
CELERY_BROKER_URL = settings.celery_broker
CELERY_RESULT_BACKEND = settings.celery_backend

ALLEGRO_RATE_LIMIT = 5
CACHE_TTL_DAYS = settings.stale_days
PROFIT_MULTIPLIER = float(settings.profitability_default_multiplier)
DEFAULT_CURRENCY = settings.default_currency

PROXY_URL = None
PROXY_USERNAME = None
PROXY_PASSWORD = None
PROXY_DIAGNOSTIC_URL = None
PROXY_DIAGNOSTIC_EXPECT = None
PROXY_DIAGNOSTIC_BODY_CHARS = 200
PROXY_DIAGNOSTIC_FORBID = ()
SCRAPER_ALERT_WEBHOOK = None


def _parse_bool(value, *, default=True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


SELENIUM_HEADLESS = _parse_bool(None, default=True)
