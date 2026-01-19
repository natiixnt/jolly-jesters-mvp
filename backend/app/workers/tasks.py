import asyncio
import json
import logging
import math
import os
import socket
import time
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlparse

from celery import Celery
from celery.exceptions import Retry, SoftTimeLimitExceeded
from celery.signals import worker_init, worker_process_init
import redis

from app.core.celery_constants import ANALYSIS_QUEUE, SCRAPER_CLOUD_QUEUE, SCRAPER_LOCAL_QUEUE
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, ProfitabilityLabel, ScrapeStatus
from app.models.product import Product
from app.services.analysis_service import (
    _ensure_product_state,
    _persist_market_data,
    _update_effective_state,
    process_analysis_run,
    record_run_task,
)
from app.services.profitability_service import calculate_profitability
from app.services.schemas import ScrapingStrategyConfig
from app.utils.allegro_scraper_http import fetch_via_http_scraper
from app.utils.local_scraper_client import check_local_scraper_health, fetch_via_local_scraper

logger = logging.getLogger(__name__)
_REDIS_CLIENT = None


def _task_soft_time_limit_seconds() -> int:
    try:
        value = int(os.getenv("SCRAPER_TASK_SOFT_TIME_LIMIT", "150"))
    except Exception:
        value = 150
    return max(30, value)


def _task_hard_time_limit_seconds() -> int:
    try:
        value = int(os.getenv("SCRAPER_TASK_HARD_TIME_LIMIT", "180"))
    except Exception:
        value = 180
    return max(_task_soft_time_limit_seconds() + 10, value)

TERMINAL_STATUSES = {
    ScrapeStatus.ok,
    ScrapeStatus.not_found,
    ScrapeStatus.blocked,
    ScrapeStatus.network_error,
    ScrapeStatus.error,
}


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _redis_client():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _REDIS_CLIENT


def _blocked_key(run_id: int) -> str:
    return f"scraper:blocked:{run_id}"


def _blocked_pause_buffer_seconds() -> int:
    try:
        value = int(os.getenv("SCRAPER_BLOCKED_PAUSE_BUFFER_SECONDS", "5"))
    except Exception:
        value = 5
    return max(0, value)


def _get_blocked_state(run_id: int) -> dict | None:
    key = _blocked_key(run_id)
    try:
        raw = _redis_client().get(key)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        item_id = int(data.get("item_id") or 0)
        until = float(data.get("until") or 0.0)
        source = data.get("source")
    except Exception:
        return None
    if not item_id or until <= time.time():
        _clear_blocked_state(run_id, item_id or None)
        return None
    return {"item_id": item_id, "until": until, "source": source}


def _set_blocked_state(run_id: int, item_id: int, delay_seconds: int, source: str | None) -> None:
    if delay_seconds <= 0:
        return
    key = _blocked_key(run_id)
    until = time.time() + delay_seconds
    payload = {"item_id": item_id, "until": until, "source": source}
    try:
        _redis_client().set(key, json.dumps(payload), ex=max(1, int(math.ceil(delay_seconds))))
    except Exception:
        return


def _clear_blocked_state(run_id: int, item_id: int | None) -> None:
    key = _blocked_key(run_id)
    try:
        if item_id is None:
            _redis_client().delete(key)
            return
        raw = _redis_client().get(key)
        if not raw:
            return
        data = json.loads(raw)
        if int(data.get("item_id") or 0) == item_id:
            _redis_client().delete(key)
    except Exception:
        return


def _maybe_pause_for_blocked_item(
    task,
    task_func,
    run: AnalysisRun,
    item: AnalysisRunItem,
    strategy_snapshot: dict | None,
    label: str,
) -> bool:
    # Do not block the whole run on a single blocked/captcha item; allow other items to proceed.
    state = _get_blocked_state(run.id)
    if state:
        _clear_blocked_state(run.id, state.get("item_id"))
    return False

def _error_message_from_result(result) -> str | None:
    if getattr(result, "error", None):
        return str(result.error)
    payload = getattr(result, "raw_payload", {}) or {}
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload.get("error"))
    return None


def _serialize_payload(payload: object) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)


def _status_and_error(result) -> tuple[ScrapeStatus, str | None]:
    if result.is_not_found:
        return ScrapeStatus.not_found, None
    if getattr(result, "blocked", False):
        return ScrapeStatus.blocked, _error_message_from_result(result) or "blocked"

    error_message = _error_message_from_result(result)
    if error_message:
        lowered = error_message.lower()
        if error_message == "network_error" or "network_error" in lowered or "connecterror" in lowered:
            return ScrapeStatus.network_error, error_message
    if result.is_temporary_error:
        return ScrapeStatus.network_error, error_message or "temporary_error"
    return ScrapeStatus.ok, None


def _timeout_retry_limit() -> int:
    try:
        value = int(os.getenv("SCRAPER_TIMEOUT_RETRIES", "3"))
    except Exception:
        value = 3
    return max(1, value)


def _timeout_retry_delay(attempt: int) -> int:
    try:
        base = int(os.getenv("SCRAPER_TIMEOUT_RETRY_DELAY", "5"))
    except Exception:
        base = 5
    return min(60, max(1, base) * max(1, attempt))


def _blocked_retry_limit() -> int:
    try:
        value = int(os.getenv("SCRAPER_BLOCKED_RETRIES", "5"))
    except Exception:
        value = 5
    return max(0, value)


def _captcha_cooldown_seconds(source: str | None) -> int:
    key = "LOCAL_SCRAPER_CAPTCHA_COOLDOWN_SECONDS"
    if source and "cloud" in source:
        key = "CLOUD_SCRAPER_CAPTCHA_COOLDOWN_SECONDS"
    try:
        value = int(os.getenv(key, "300"))
    except Exception:
        value = 300
    return max(1, value)


def _blocked_retry_delay(result, attempt: int) -> int:
    payload = getattr(result, "raw_payload", {}) or {}
    if isinstance(payload, dict):
        retry_after = payload.get("retry_after_seconds") or payload.get("retry_after")
        if retry_after is not None:
            try:
                return max(1, int(math.ceil(float(retry_after))))
            except Exception:
                pass
    error_message = _error_message_from_result(result) or ""
    if "captcha" in error_message.lower():
        return _captcha_cooldown_seconds(getattr(result, "source", None))
    try:
        base = int(os.getenv("SCRAPER_BLOCKED_RETRY_DELAY", "60"))
    except Exception:
        base = 60
    return max(1, base) * max(1, attempt)


def _maybe_retry_blocked(
    task,
    db: SessionLocal,
    run: AnalysisRun,
    item: AnalysisRunItem,
    result,
    label: str,
) -> None:
    if not task:
        return
    if not getattr(result, "blocked", False):
        return
    limit = _blocked_retry_limit()
    if limit <= 0:
        return
    current_attempt = (task.request.retries or 0) + 1
    if current_attempt >= limit:
        return
    delay = _blocked_retry_delay(result, current_attempt)
    retry_note = f"blocked_retry:{current_attempt}/{limit}"
    error_message = _error_message_from_result(result)
    if error_message:
        retry_note = f"{retry_note}:{error_message}"
    item.source = AnalysisItemSource.scraping
    item.scrape_status = ScrapeStatus.in_progress
    item.error_message = retry_note
    db.commit()
    logger.warning(
        "%s blocked retry scheduled item_id=%s ean=%s attempt=%s/%s delay=%ss",
        label,
        item.id,
        item.ean,
        current_attempt,
        limit,
        delay,
    )
    raise task.retry(countdown=delay, max_retries=limit - 1)


def _is_timeout_error(error_message: str | None, payload: dict | None) -> bool:
    if error_message:
        lowered = str(error_message).lower()
        if "timeout" in lowered or "timed out" in lowered:
            return True
    if isinstance(payload, dict):
        error_type = str(payload.get("error_type") or "").lower()
        if "timeout" in error_type:
            return True
    return False


def _maybe_retry_timeout(task, db: SessionLocal, item: AnalysisRunItem, result, label: str) -> None:
    if not task:
        return
    if getattr(result, "blocked", False):
        return
    payload = getattr(result, "raw_payload", {}) or {}
    error_message = _error_message_from_result(result)
    if not _is_timeout_error(error_message, payload if isinstance(payload, dict) else None):
        return
    limit = _timeout_retry_limit()
    current_attempt = (task.request.retries or 0) + 1
    if current_attempt >= limit:
        return
    delay = _timeout_retry_delay(current_attempt)
    retry_note = f"timeout_retry:{current_attempt}/{limit}"
    if error_message:
        retry_note = f"{retry_note}:{error_message}"
    item.source = AnalysisItemSource.scraping
    item.scrape_status = ScrapeStatus.in_progress
    item.error_message = retry_note
    db.commit()
    logger.warning(
        "%s timeout retry scheduled item_id=%s ean=%s attempt=%s/%s delay=%ss",
        label,
        item.id,
        item.ean,
        current_attempt,
        limit,
        delay,
    )
    raise task.retry(countdown=delay, max_retries=limit - 1)


def _log_scrape_outcome(label: str, item: AnalysisRunItem, result, status: ScrapeStatus, error_message: str | None) -> None:
    payload = getattr(result, "raw_payload", {}) or {}
    fingerprint_id = getattr(result, "fingerprint_id", None) or payload.get("fingerprint_id")
    logger.info(
        "%s item_id=%s ean=%s status=%s not_found=%s blocked=%s temp_error=%s error=%s http_status=%s source=%s fingerprint_id=%s",
        label,
        item.id,
        item.ean,
        status,
        bool(getattr(result, "is_not_found", False)),
        bool(getattr(result, "blocked", False)),
        bool(getattr(result, "is_temporary_error", False)),
        error_message,
        payload.get("status_code"),
        getattr(result, "source", None),
        fingerprint_id,
    )


def _handle_unexpected_exception(
    db: SessionLocal,
    run: AnalysisRun | None,
    item: AnalysisRunItem | None,
    prev_status: ScrapeStatus | None,
    label: str,
    exc: Exception,
) -> None:
    logger.exception("%s unexpected error item_id=%s ean=%s", label, getattr(item, "id", None), getattr(item, "ean", None))
    if not run or not item:
        return
    if run.status in {AnalysisStatus.failed, AnalysisStatus.canceled, AnalysisStatus.completed}:
        db.commit()
        return
    item.source = AnalysisItemSource.error
    item.scrape_status = ScrapeStatus.error
    item.error_message = f"unexpected_error:{type(exc).__name__}"
    _finalize_item(db, run, item, prev_status)


def _handle_timeout(
    db: SessionLocal,
    run: AnalysisRun | None,
    item: AnalysisRunItem | None,
    prev_status: ScrapeStatus | None,
    label: str,
    stage: str,
    duration_seconds: float | None = None,
    fingerprint_id: str | None = None,
) -> None:
    if not run or not item:
        return
    message = stage if stage else "timeout"
    if duration_seconds is not None:
        message = f"{message}:{round(duration_seconds, 2)}s"
    item.source = AnalysisItemSource.error
    item.scrape_status = ScrapeStatus.network_error
    item.error_message = message
    item.allegro_price = None
    item.allegro_sold_count = None
    item.profitability_score = None
    item.profitability_label = None
    logger.warning(
        "%s timeout item_id=%s ean=%s stage=%s duration=%s fingerprint_id=%s",
        label,
        getattr(item, "id", None),
        getattr(item, "ean", None),
        stage,
        duration_seconds,
        fingerprint_id,
    )
    _clear_blocked_state(run.id, item.id)
    _finalize_item(db, run, item, prev_status)


def _maybe_fail_run_on_blocked(run: AnalysisRun, error_message: str | None) -> None:
    if not _env_flag_enabled("LOCAL_SCRAPER_STOP_ON_BLOCKED", default=True):
        return
    if run.status in {AnalysisStatus.failed, AnalysisStatus.canceled, AnalysisStatus.completed}:
        return
    reason = error_message or "blocked"
    run.status = AnalysisStatus.failed
    run.error_message = reason
    run.finished_at = datetime.now(timezone.utc)


def _should_fallback_local(strategy_snapshot: dict | None) -> bool:
    if not strategy_snapshot:
        return False
    if not strategy_snapshot.get("use_local_scraper", False):
        return False
    return bool(settings.LOCAL_SCRAPER_ENABLED and settings.LOCAL_SCRAPER_URL)


def _apply_scrape_result(
    db: SessionLocal,
    item: AnalysisRunItem,
    product: Product,
    category: Category,
    state,
    result,
) -> None:
    price = result.price
    sold_count = result.sold_count
    market_data = _persist_market_data(
        db=db,
        product=product,
        source=result.source,
        price=price,
        sold_count=sold_count,
        is_not_found=result.is_not_found,
        raw_payload=result.raw_payload,
        last_checked_at=result.last_checked_at,
    )

    if result.is_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
        source_val = AnalysisItemSource.not_found
        item.scrape_status = ScrapeStatus.not_found
    else:
        purchase_price = item.purchase_price_pln or item.input_purchase_price
        score, label = calculate_profitability(purchase_price, price, sold_count, category)
        source_val = AnalysisItemSource.scraping
        item.scrape_status = ScrapeStatus.ok

    _update_effective_state(state, market_data, score, label)

    item.source = source_val
    item.allegro_price = price
    item.allegro_sold_count = sold_count
    item.profitability_score = score
    item.profitability_label = label
    item.error_message = None


def _finalize_item(db: SessionLocal, run: AnalysisRun, item: AnalysisRunItem, prev_status: ScrapeStatus | None) -> None:
    if run.status in {AnalysisStatus.canceled, AnalysisStatus.failed}:
        db.commit()
        return
    if prev_status not in TERMINAL_STATUSES:
        run.processed_products += 1
    if run.processed_products >= run.total_products and run.status != AnalysisStatus.completed:
        run.status = AnalysisStatus.completed
        run.finished_at = datetime.now(timezone.utc)
    db.commit()

celery_app = Celery(
    "jolly-jesters",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
)

celery_app.conf.task_default_queue = ANALYSIS_QUEUE
celery_app.conf.task_routes = {
    "app.workers.tasks.run_analysis_task": {"queue": ANALYSIS_QUEUE},
    "app.workers.tasks.scrape_one_local": {"queue": SCRAPER_LOCAL_QUEUE},
    "app.workers.tasks.scrape_one_cloud": {"queue": SCRAPER_CLOUD_QUEUE},
}

celery = celery_app  # alias for CLI


@worker_process_init.connect
def _reset_db_connections(**kwargs):
    engine.dispose()


@worker_init.connect
def _log_local_scraper_config(**kwargs):
    logger.info(
        "LOCAL_SCRAPER_CONFIG enabled=%s url=%s",
        settings.LOCAL_SCRAPER_ENABLED,
        settings.LOCAL_SCRAPER_URL,
    )
    _log_local_scraper_connectivity()


def _log_local_scraper_connectivity() -> None:
    host = None
    if settings.LOCAL_SCRAPER_URL:
        try:
            host = urlparse(settings.LOCAL_SCRAPER_URL).hostname
        except Exception:
            host = None
    try:
        if host:
            infos = socket.getaddrinfo(host, None)
            resolved_ips = sorted({info[4][0] for info in infos})
            logger.info("LOCAL_SCRAPER_DNS host=%s ips=%s", host, resolved_ips)
        else:
            logger.info("LOCAL_SCRAPER_DNS host=missing")
    except Exception as exc:
        logger.warning(
            "LOCAL_SCRAPER_DNS host=%s error=%s err=%r",
            host,
            type(exc).__name__,
            exc,
        )

    health = check_local_scraper_health(timeout_seconds=2.0)
    logger.info(
        "LOCAL_SCRAPER_HEALTH url=%s status=%s status_code=%s",
        health.get("url"),
        health.get("status"),
        health.get("status_code"),
    )


@celery_app.task(acks_late=True)
def run_analysis_task(run_id: int, mode: str = "mixed"):
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            logger.warning("RUN_ANALYSIS_TASK missing_run run_id=%s", run_id)
            return
        # Clear any lingering blocked/cooldown state for this run so new uploads start immediately.
        _clear_blocked_state(run.id, None)
        if run.status == AnalysisStatus.canceled:
            logger.info("RUN_ANALYSIS_TASK canceled run_id=%s", run_id)
            return

        def _enqueue_cloud_scrape(item: AnalysisRunItem, strategy: ScrapingStrategyConfig):
            result = scrape_one_cloud.delay(item.ean, item.id, asdict(strategy))
            record_run_task(db, run, result.id, "cloud", item=item, ean=item.ean)
            return result

        def _enqueue_local_scrape(item: AnalysisRunItem, strategy: ScrapingStrategyConfig):
            result = scrape_one_local.delay(item.ean, item.id, asdict(strategy))
            record_run_task(db, run, result.id, "local", item=item, ean=item.ean)
            return result

        process_analysis_run(
            db,
            run_id=run_id,
            mode=mode,
            enqueue_cloud_scrape=_enqueue_cloud_scrape,
            enqueue_local_scrape=_enqueue_local_scrape,
        )
    finally:
        db.close()


@celery_app.task(
    acks_late=True,
    bind=True,
    soft_time_limit=_task_soft_time_limit_seconds(),
    time_limit=_task_hard_time_limit_seconds(),
)
def scrape_one_local(
    self,
    ean: str,
    run_item_id: int,
    strategy_snapshot: dict | None = None,
) -> None:
    db = SessionLocal()
    item = None
    run = None
    prev_status = None
    try:
        item = db.query(AnalysisRunItem).filter(AnalysisRunItem.id == run_item_id).first()
        if not item:
            logger.warning("LOCAL_SCRAPER_TASK missing_item id=%s ean=%s", run_item_id, ean)
            return
        prev_status = item.scrape_status
        if prev_status in TERMINAL_STATUSES:
            logger.info("LOCAL_SCRAPER_TASK skip terminal item_id=%s status=%s", item.id, prev_status)
            return

        run = db.query(AnalysisRun).filter(AnalysisRun.id == item.analysis_run_id).first()
        if not run:
            logger.warning("LOCAL_SCRAPER_TASK missing_run item_id=%s run_id=%s", item.id, item.analysis_run_id)
            return
        if run.status in {AnalysisStatus.failed, AnalysisStatus.completed, AnalysisStatus.canceled}:
            logger.info(
                "LOCAL_SCRAPER_TASK skip run_status=%s item_id=%s",
                run.status,
                item.id,
            )
            return

        if _maybe_pause_for_blocked_item(self, scrape_one_local, run, item, strategy_snapshot, "LOCAL_SCRAPER_TASK"):
            return

        category = db.query(Category).filter(Category.id == run.category_id).first()
        if not category:
            item.source = AnalysisItemSource.error
            item.error_message = "Kategoria nie została znaleziona"
            item.scrape_status = ScrapeStatus.error
            _finalize_item(db, run, item, prev_status)
            return

        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            item.source = AnalysisItemSource.error
            item.error_message = "Brak produktu dla wiersza"
            item.scrape_status = ScrapeStatus.error
            _finalize_item(db, run, item, prev_status)
            return

        state = _ensure_product_state(db, product)
        item.scrape_status = ScrapeStatus.in_progress
        item.error_message = None
        db.commit()
        result = asyncio.run(fetch_via_local_scraper(item.ean))
        if result.is_temporary_error or getattr(result, "blocked", False):
            _maybe_retry_blocked(self, db, run, item, result, "LOCAL_SCRAPER_TASK")
            _maybe_retry_timeout(self, db, item, result, "LOCAL_SCRAPER_TASK")
            status, error_message = _status_and_error(result)
            _log_scrape_outcome("LOCAL_SCRAPER_RESULT", item, result, status, error_message)
            item.source = AnalysisItemSource.error
            item.scrape_status = status
            item.allegro_price = None
            item.allegro_sold_count = None
            item.profitability_score = None
            item.profitability_label = None
            item.error_message = error_message or _serialize_payload(result.raw_payload)
            if status == ScrapeStatus.blocked:
                _maybe_fail_run_on_blocked(run, item.error_message)
            _clear_blocked_state(run.id, item.id)
            _finalize_item(db, run, item, prev_status)
            return

        status = ScrapeStatus.not_found if result.is_not_found else ScrapeStatus.ok
        _log_scrape_outcome("LOCAL_SCRAPER_RESULT", item, result, status, None)
        _apply_scrape_result(db, item, product, category, state, result)
        _clear_blocked_state(run.id, item.id)
        _finalize_item(db, run, item, prev_status)
    except SoftTimeLimitExceeded as exc:
        _handle_timeout(db, run, item, prev_status, "LOCAL_SCRAPER_TASK", "celery_soft_timeout", fingerprint_id=None)
    except Retry:
        raise
    except Exception as exc:
        _handle_unexpected_exception(db, run, item, prev_status, "LOCAL_SCRAPER_TASK", exc)
    finally:
        db.close()


@celery_app.task(
    acks_late=True,
    bind=True,
    soft_time_limit=_task_soft_time_limit_seconds(),
    time_limit=_task_hard_time_limit_seconds(),
)
def scrape_one_cloud(
    self,
    ean: str,
    run_item_id: int,
    strategy_snapshot: dict | None = None,
) -> None:
    db = SessionLocal()
    item = None
    run = None
    prev_status = None
    try:
        item = db.query(AnalysisRunItem).filter(AnalysisRunItem.id == run_item_id).first()
        if not item:
            logger.warning("CLOUD_SCRAPER_TASK missing_item id=%s ean=%s", run_item_id, ean)
            return
        prev_status = item.scrape_status
        if prev_status in TERMINAL_STATUSES:
            logger.info("CLOUD_SCRAPER_TASK skip terminal item_id=%s status=%s", item.id, prev_status)
            return

        run = db.query(AnalysisRun).filter(AnalysisRun.id == item.analysis_run_id).first()
        if not run:
            logger.warning("CLOUD_SCRAPER_TASK missing_run item_id=%s run_id=%s", item.id, item.analysis_run_id)
            return
        if run.status in {AnalysisStatus.failed, AnalysisStatus.completed, AnalysisStatus.canceled}:
            logger.info("CLOUD_SCRAPER_TASK skip run_status=%s item_id=%s", run.status, item.id)
            return

        if _maybe_pause_for_blocked_item(self, scrape_one_cloud, run, item, strategy_snapshot, "CLOUD_SCRAPER_TASK"):
            return

        category = db.query(Category).filter(Category.id == run.category_id).first()
        if not category:
            item.source = AnalysisItemSource.error
            item.error_message = "Kategoria nie została znaleziona"
            item.scrape_status = ScrapeStatus.error
            _finalize_item(db, run, item, prev_status)
            return

        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            item.source = AnalysisItemSource.error
            item.error_message = "Brak produktu dla wiersza"
            item.scrape_status = ScrapeStatus.error
            _finalize_item(db, run, item, prev_status)
            return

        state = _ensure_product_state(db, product)
        item.scrape_status = ScrapeStatus.in_progress
        item.error_message = None
        db.commit()
        result = asyncio.run(fetch_via_http_scraper(item.ean))

        if result.is_temporary_error or getattr(result, "blocked", False):
            if _should_fallback_local(strategy_snapshot):
                item.source = AnalysisItemSource.scraping
                item.scrape_status = ScrapeStatus.pending
                item.error_message = None
                db.commit()
                scrape_one_local.delay(item.ean, item.id, strategy_snapshot)
                return

            _maybe_retry_blocked(self, db, run, item, result, "CLOUD_SCRAPER_TASK")
            _maybe_retry_timeout(self, db, item, result, "CLOUD_SCRAPER_TASK")
            status, error_message = _status_and_error(result)
            _log_scrape_outcome("CLOUD_SCRAPER_RESULT", item, result, status, error_message)
            item.source = AnalysisItemSource.error
            item.scrape_status = status
            item.allegro_price = None
            item.allegro_sold_count = None
            item.profitability_score = None
            item.profitability_label = None
            item.error_message = error_message or _serialize_payload(result.raw_payload)
            _clear_blocked_state(run.id, item.id)
            _finalize_item(db, run, item, prev_status)
            return

        status = ScrapeStatus.not_found if result.is_not_found else ScrapeStatus.ok
        _log_scrape_outcome("CLOUD_SCRAPER_RESULT", item, result, status, None)
        _apply_scrape_result(db, item, product, category, state, result)
        _clear_blocked_state(run.id, item.id)
        _finalize_item(db, run, item, prev_status)
    except SoftTimeLimitExceeded as exc:
        _handle_timeout(db, run, item, prev_status, "CLOUD_SCRAPER_TASK", "celery_soft_timeout", fingerprint_id=None)
    except Retry:
        raise
    except Exception as exc:
        _handle_unexpected_exception(db, run, item, prev_status, "CLOUD_SCRAPER_TASK", exc)
    finally:
        db.close()
