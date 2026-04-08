from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import Celery
from celery.signals import worker_process_init
from sqlalchemy.orm import Session

from app.core.celery_constants import ANALYSIS_QUEUE
from app.core.config import settings
from sqlalchemy.orm import selectinload

from app.db.session import SessionLocal, engine
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, ScrapeStatus
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.services import settings_service
from app.services.analysis_service import (
    _ensure_product_state,
    _persist_market_data,
    _update_effective_state,
)
from app.services.profitability_service import evaluate_profitability
from app.services.schemas import AllegroResult
from app.services.settings_service import get_settings
from app.services.stoploss_service import StopLossChecker, StopLossConfig
from app.services import proxy_pool_service
from app.providers import get_provider
from app.services.alerting_service import alert_stoploss, notify_run_completed
from app.services.billing_service import record_run_usage
from app.services.circuit_breaker import CircuitBreaker
from app.utils.allegro_scraper_client import fetch_via_allegro_scraper

logger = logging.getLogger(__name__)

celery_app = Celery(
    "jolly-jesters",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
)
celery_app.conf.task_default_queue = ANALYSIS_QUEUE
celery_app.conf.beat_schedule = {
    "refresh-monitored-eans": {
        "task": "app.workers.tasks.refresh_monitored_eans",
        "schedule": 60.0,  # every minute, checks which EANs are due
    },
}
celery = celery_app

_scraper_breaker = CircuitBreaker(name="scraper", failure_threshold=10, recovery_timeout=60)


_worker_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop


@worker_process_init.connect
def _reset_db_connections(**kwargs):
    engine.dispose()
    global _worker_loop
    _worker_loop = asyncio.new_event_loop()


def _error_result(ean: str, error: str) -> AllegroResult:
    return AllegroResult(
        ean=ean,
        status="error",
        total_offer_count=None,
        products=[],
        price=None,
        sold_count=None,
        is_not_found=False,
        is_temporary_error=False,
        raw_payload={"error": error},
        error=error,
        source="allegro_scraper",
    )


def _extract_offer_count(raw_payload: dict | None) -> int | None:
    try:
        products = (raw_payload or {}).get("products")
        if isinstance(products, list):
            return len(products)
    except Exception:
        return None
    return None


def _is_fresh_market_data(
    market_data: ProductMarketData | None,
    cache_days: int,
    now: datetime | None = None,
) -> bool:
    if cache_days <= 0 or not market_data or not market_data.last_checked_at:
        return False
    current = now or datetime.now(timezone.utc)
    checked_at = market_data.last_checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at >= (current - timedelta(days=cache_days))


def _should_fetch_from_scraper(
    db_only_mode: bool,
    market_data: ProductMarketData | None,
    cache_days: int,
    now: datetime | None = None,
) -> bool:
    if db_only_mode:
        return False
    return not _is_fresh_market_data(market_data, cache_days=cache_days, now=now)


def _resolve_cache_days(run: AnalysisRun, db_only_mode: bool, db: Session) -> int:
    if db_only_mode:
        meta = run.run_metadata if isinstance(run.run_metadata, dict) else {}
        raw_days = meta.get("cache_days", 30)
        try:
            days = int(raw_days)
        except Exception:
            return 30
        return 30 if days <= 0 else days

    settings_record = settings_service.get_settings(db)
    try:
        return max(0, int(settings_record.cache_ttl_days))
    except Exception:
        return 30


def _apply_cached_market_data(
    item: AnalysisRunItem,
    category: Category,
    market_data: ProductMarketData | None,
) -> None:
    purchase_price = item.purchase_price_pln or item.input_purchase_price
    if not market_data:
        item.source = AnalysisItemSource.error
        item.scrape_status = ScrapeStatus.error
        item.error_message = "missing_cached_data"
        item.allegro_price = None
        item.allegro_sold_count = None
        item.profitability_score = None
        item.profitability_label = None
        return

    if market_data.is_not_found:
        item.source = AnalysisItemSource.baza
        item.scrape_status = ScrapeStatus.not_found
        item.error_message = None
        item.allegro_price = None
        item.allegro_sold_count = None
        item.profitability_score = None
        item.profitability_label = None
        return

    offer_count = _extract_offer_count(market_data.raw_payload)
    evaluation = evaluate_profitability(
        purchase_price=purchase_price,
        allegro_price=market_data.allegro_price,
        sold_count=market_data.allegro_sold_count,
        category=category,
        offer_count=offer_count,
    )
    item.source = AnalysisItemSource.baza
    item.scrape_status = ScrapeStatus.ok
    item.error_message = None
    item.allegro_price = market_data.allegro_price
    item.allegro_sold_count = market_data.allegro_sold_count
    item.profitability_score = evaluation.score
    item.profitability_label = evaluation.label


def _apply_scraped_result(
    db,
    item: AnalysisRunItem,
    product: Product,
    category: Category,
    result: AllegroResult,
) -> None:
    purchase_price = item.purchase_price_pln or item.input_purchase_price

    if result.is_temporary_error and not result.is_not_found:
        item.source = AnalysisItemSource.error
        item.scrape_status = ScrapeStatus.network_error
        item.error_message = result.error or "temporary_error"
        item.allegro_price = None
        item.allegro_sold_count = None
        item.profitability_score = None
        item.profitability_label = None
        # -- metering (even for errors) --
        item.latency_ms = result.duration_ms
        item.captcha_solves = result.captcha_solves
        item.retries = result.retries
        item.attempts = result.attempts
        return

    if result.is_not_found:
        item.source = AnalysisItemSource.not_found
        item.scrape_status = ScrapeStatus.not_found
        item.allegro_price = None
        item.allegro_sold_count = None
        item.profitability_score = None
        item.profitability_label = None
    else:
        offer_count = len(result.products) if result.products else 0
        evaluation = evaluate_profitability(
            purchase_price=purchase_price,
            allegro_price=result.price,
            sold_count=result.sold_count,
            category=category,
            offer_count=offer_count,
        )
        item.source = AnalysisItemSource.scraping
        item.scrape_status = ScrapeStatus.ok
        item.allegro_price = result.price
        item.allegro_sold_count = result.sold_count
        item.profitability_score = evaluation.score
        item.profitability_label = evaluation.label

    market_data = _persist_market_data(
        db=db,
        product=product,
        source=result.source,
        price=result.price,
        sold_count=result.sold_count,
        is_not_found=result.is_not_found,
        raw_payload=result.raw_payload,
        last_checked_at=result.scraped_at or datetime.now(timezone.utc),
    )
    _update_effective_state(
        product.effective_state,  # type: ignore[arg-type]
        market_data,
        item.profitability_score,
        item.profitability_label,
    )
    item.error_message = result.error

    # -- metering --
    item.latency_ms = result.duration_ms
    item.captcha_solves = result.captcha_solves
    item.retries = result.retries
    item.attempts = result.attempts


def _update_run_metadata(
    run: AnalysisRun,
    db_only_mode: bool,
    cache_days: int,
    scraper_request_count: int,
    db_cache_hit_count: int,
    db_only_item_count: int,
) -> None:
    metadata = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    metadata = dict(metadata)
    metadata.update(
        {
            "db_only_mode": db_only_mode,
            "cache_days": cache_days,
            "scraper_request_count": scraper_request_count,
            "db_cache_hit_count": db_cache_hit_count,
            "db_only_item_count": db_only_item_count,
        }
    )
    run.run_metadata = metadata


def _acquire_run_lock(run_id: int) -> bool:
    """Acquire a Redis-based distributed lock for run processing."""
    import redis
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        return bool(r.set(f"run_lock:{run_id}", "1", nx=True, ex=3600))
    except Exception:
        return True  # fallback: allow if Redis unavailable


def _release_run_lock(run_id: int) -> None:
    import redis
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
        r.delete(f"run_lock:{run_id}")
    except Exception:
        pass


@celery_app.task(acks_late=True, bind=True)
def run_analysis_task(self, run_id: int):
    if not _acquire_run_lock(run_id):
        logger.warning("RUN_TASK run_id=%s already locked, skipping", run_id)
        return

    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            logger.warning("RUN_TASK missing run_id=%s", run_id)
            return
        if run.status == AnalysisStatus.canceled:
            logger.info("RUN_TASK canceled before start run_id=%s", run.id)
            return

        category = db.query(Category).filter(Category.id == run.category_id).first()
        if not category:
            run.status = AnalysisStatus.failed
            run.error_message = "Kategoria nie zostala znaleziona"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        items = (
            db.query(AnalysisRunItem)
            .options(
                selectinload(AnalysisRunItem.product)
                .selectinload(Product.effective_state)
                .selectinload(ProductEffectiveState.last_market_data)
            )
            .filter(AnalysisRunItem.analysis_run_id == run.id)
            .order_by(AnalysisRunItem.row_number)
            .all()
        )
        if not items:
            run.status = AnalysisStatus.failed
            run.error_message = "Brak pozycji do analizy"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        db_only_mode = (run.mode or "").strip().lower() == "cached"
        cache_days = _resolve_cache_days(run, db_only_mode=db_only_mode, db=db)
        scraper_request_count = 0
        db_cache_hit_count = 0
        db_only_item_count = 0

        if db_only_mode:
            logger.info("DB_ONLY_MODE enabled, skipping scraper run_id=%s cache_days=%s", run.id, cache_days)
        else:
            logger.info("RUN_TASK live mode run_id=%s cache_days=%s", run.id, cache_days)

        run.status = AnalysisStatus.running
        run.error_message = None
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        # -- stop-loss init --
        setting = get_settings(db)
        stoploss = StopLossChecker(StopLossConfig(
            enabled=setting.stoploss_enabled,
            window_size=setting.stoploss_window_size,
            max_error_rate=float(setting.stoploss_max_error_rate),
            max_captcha_rate=float(setting.stoploss_max_captcha_rate),
            max_consecutive_errors=setting.stoploss_max_consecutive_errors,
            max_retry_rate=float(setting.stoploss_max_retry_rate),
            max_blocked_rate=float(setting.stoploss_max_blocked_rate),
            max_cost_per_1000=float(setting.stoploss_max_cost_per_1000),
        ))

        for item in items:
            db.refresh(run)
            if run.status == AnalysisStatus.canceled:
                logger.info("RUN_TASK canceled mid-run run_id=%s", run.id)
                break

            product = item.product  # eager-loaded via selectinload
            if not product:
                item.source = AnalysisItemSource.error
                item.scrape_status = ScrapeStatus.error
                item.error_message = "Brak produktu dla wiersza"
                run.processed_products += 1
                db.commit()
                continue

            state = _ensure_product_state(db, product)
            product.effective_state = state
            item.scrape_status = ScrapeStatus.in_progress
            item.error_message = None
            db.commit()

            market_data = state.last_market_data if state else None
            result = None

            now = datetime.now(timezone.utc)
            if _should_fetch_from_scraper(
                db_only_mode=db_only_mode,
                market_data=market_data,
                cache_days=cache_days,
                now=now,
            ):
                scraper_request_count += 1
                if _scraper_breaker.is_open():
                    result = _error_result(item.ean, "circuit_breaker_open")
                else:
                    try:
                        provider = get_provider()
                        result = _get_loop().run_until_complete(provider.fetch(item.ean, run_id=str(run.id)))
                        _scraper_breaker.record_success()
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.exception("RUN_TASK unexpected error ean=%s", item.ean)
                        result = _error_result(item.ean, f"unexpected:{type(exc).__name__}")
                        _scraper_breaker.record_failure()

                _apply_scraped_result(db, item, product, category, result)
            else:
                _apply_cached_market_data(item, category, market_data)
                if db_only_mode:
                    db_only_item_count += 1
                else:
                    db_cache_hit_count += 1
                    logger.info("RUN_TASK db_cache hit run_id=%s ean=%s", run.id, item.ean)

            run.processed_products += 1

            # -- proxy scoring (best-effort) --
            if result and getattr(result, 'proxy_url_hash', None):
                try:
                    if result.proxy_success:
                        proxy_pool_service.record_success(db, result.proxy_url_hash)
                    else:
                        proxy_pool_service.record_failure(db, result.proxy_url_hash, result.error or "")
                except Exception:
                    logger.debug("PROXY_SCORING failed for hash=%s", getattr(result, 'proxy_url_hash', '?'))

            # -- stop-loss check --
            verdict = stoploss.record(
                item.scrape_status,
                captcha_solves=getattr(result, 'captcha_solves', 0) or 0 if result else 0,
                retries=getattr(result, 'retries', 0) or 0 if result else 0,
                is_blocked=item.scrape_status == ScrapeStatus.blocked,
                cost=getattr(result, 'cost', 0.0) or 0.0 if result else 0.0,
            )
            if verdict.should_stop:
                logger.warning(
                    "STOP_LOSS triggered run_id=%s reason=%s details=%s",
                    run.id, verdict.reason, verdict.details,
                )
                run.status = AnalysisStatus.stopped
                run.run_metadata = {
                    **(run.run_metadata or {}),
                    "stop_reason": verdict.reason,
                    "stop_details": verdict.details,
                    "stopped_at_item": item.row_number,
                }
                run.finished_at = datetime.now(timezone.utc)
                # mark remaining pending items as stopped_by_guardrail
                db.query(AnalysisRunItem).filter(
                    AnalysisRunItem.analysis_run_id == run.id,
                    AnalysisRunItem.scrape_status == ScrapeStatus.pending,
                ).update(
                    {"scrape_status": ScrapeStatus.stopped_by_guardrail},
                    synchronize_session="fetch",
                )
                db.commit()
                try:
                    alert_stoploss(run.id, verdict.reason, verdict.details or {})
                except Exception:
                    logger.debug("ALERT_WEBHOOK failed for run_id=%s", run.id)
                break

            db.commit()

        # update metadata once at end (not per-item)
        _update_run_metadata(
            run,
            db_only_mode=db_only_mode,
            cache_days=cache_days,
            scraper_request_count=scraper_request_count,
            db_cache_hit_count=db_cache_hit_count,
            db_only_item_count=db_only_item_count,
        )

        if run.status not in {AnalysisStatus.canceled, AnalysisStatus.stopped}:
            run.status = AnalysisStatus.completed
            run.finished_at = datetime.now(timezone.utc)
            _update_run_metadata(
                run,
                db_only_mode=db_only_mode,
                cache_days=cache_days,
                scraper_request_count=scraper_request_count,
                db_cache_hit_count=db_cache_hit_count,
                db_only_item_count=db_only_item_count,
            )
            db.commit()

        # record usage for billing + send notification (best-effort)
        if run.status in {AnalysisStatus.completed, AnalysisStatus.stopped, AnalysisStatus.failed}:
            try:
                record_run_usage(db, run.id)
            except Exception:
                logger.exception("BILLING usage recording failed run_id=%s", run.id)
            try:
                cat_name = category.name if category else ""
                notify_run_completed(run.id, run.status.value, run.processed_products, run.total_products, cat_name)
            except Exception:
                logger.debug("NOTIFICATION failed run_id=%s", run.id)
    finally:
        db.close()
        _release_run_lock(run_id)


@celery_app.task(acks_late=True)
def refresh_monitored_eans():
    """Periodic task: scrape EANs that are due for refresh."""
    from app.services.monitoring_service import get_due_eans, mark_scraped
    from app.services.alert_engine import evaluate_rules_for_ean, get_previous_price

    db = SessionLocal()
    try:
        due = get_due_eans(db, limit=50)
        if not due:
            return

        logger.info("MONITOR_REFRESH found %d EANs due", len(due))
        provider = get_provider()
        loop = _get_loop()

        for m in due:
            try:
                prev_price = get_previous_price(db, m.ean)
                result = loop.run_until_complete(provider.fetch(m.ean))

                # evaluate alert rules
                evaluate_rules_for_ean(
                    db=db,
                    tenant_id=str(m.tenant_id),
                    ean=m.ean,
                    current_price=result.price,
                    previous_price=prev_price,
                    sold_count=result.sold_count,
                    is_not_found=result.is_not_found,
                )

                mark_scraped(db, m)
                logger.info("MONITOR_REFRESH ean=%s price=%s", m.ean, result.price)

            except Exception:
                logger.exception("MONITOR_REFRESH failed ean=%s", m.ean)
                # still mark as scraped to avoid infinite retry loop
                mark_scraped(db, m)
    finally:
        db.close()
