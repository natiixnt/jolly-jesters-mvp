from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from celery import Celery
from celery.signals import worker_process_init

from app.core.celery_constants import ANALYSIS_QUEUE
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, ScrapeStatus
from app.models.product import Product
from app.services.analysis_service import (
    _ensure_product_state,
    _persist_market_data,
    _update_effective_state,
)
from app.services.profitability_service import calculate_profitability
from app.services.schemas import AllegroResult
from app.services.settings_service import get_settings
from app.services.stoploss_service import StopLossChecker, StopLossConfig
from app.services import proxy_pool_service
from app.providers import get_provider
from app.services.alerting_service import alert_stoploss
from app.services.billing_service import record_run_usage
from app.utils.allegro_scraper_client import fetch_via_allegro_scraper

logger = logging.getLogger(__name__)

celery_app = Celery(
    "jolly-jesters",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
)
celery_app.conf.task_default_queue = ANALYSIS_QUEUE
celery = celery_app


@worker_process_init.connect
def _reset_db_connections(**kwargs):
    engine.dispose()


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


def _apply_result(
    db: SessionLocal,
    run: AnalysisRun,
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
        score, label = calculate_profitability(purchase_price, result.price, result.sold_count, category)
        item.source = AnalysisItemSource.scraping
        item.scrape_status = ScrapeStatus.ok
        item.allegro_price = result.price
        item.allegro_sold_count = result.sold_count
        item.profitability_score = score
        item.profitability_label = label

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


@celery_app.task(acks_late=True, bind=True)
def run_analysis_task(self, run_id: int):
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
            run.error_message = "Kategoria nie została znaleziona"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        items = (
            db.query(AnalysisRunItem)
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
        ))

        for item in items:
            db.refresh(run)
            if run.status == AnalysisStatus.canceled:
                logger.info("RUN_TASK canceled mid-run run_id=%s", run.id)
                break

            product = db.query(Product).filter(Product.id == item.product_id).first()
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

            try:
                provider = get_provider()
                result = asyncio.run(provider.fetch(item.ean, run_id=str(run.id)))
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("RUN_TASK unexpected error ean=%s", item.ean)
                result = _error_result(item.ean, f"unexpected:{type(exc).__name__}")

            _apply_result(db, run, item, product, category, result)
            run.processed_products += 1

            # -- proxy scoring --
            if result.proxy_url_hash:
                try:
                    if result.proxy_success:
                        proxy_pool_service.record_success(db, result.proxy_url_hash)
                    else:
                        proxy_pool_service.record_failure(db, result.proxy_url_hash, result.error or "")
                except Exception:
                    pass  # proxy scoring is best-effort

            # -- stop-loss check --
            verdict = stoploss.record(
                item.scrape_status,
                captcha_solves=result.captcha_solves or 0,
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
                db.commit()
                # fire alert webhook (best-effort)
                try:
                    alert_stoploss(run.id, verdict.reason, verdict.details or {})
                except Exception:
                    pass
                break

            db.commit()

        if run.status not in {AnalysisStatus.canceled, AnalysisStatus.stopped}:
            run.status = AnalysisStatus.completed
            run.finished_at = datetime.now(timezone.utc)
            db.commit()

        # record usage for billing (best-effort)
        if run.status in {AnalysisStatus.completed, AnalysisStatus.stopped}:
            try:
                record_run_usage(db, run.id)
            except Exception:
                logger.exception("BILLING usage recording failed run_id=%s", run.id)
    finally:
        db.close()
