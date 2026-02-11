from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import Celery
from celery.signals import worker_process_init
from sqlalchemy.orm import Session

from app.core.celery_constants import ANALYSIS_QUEUE
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, ScrapeStatus
from app.models.product import Product
from app.models.product_market_data import ProductMarketData
from app.services import settings_service
from app.services.analysis_service import (
    _ensure_product_state,
    _persist_market_data,
    _update_effective_state,
)
from app.services.profitability_service import evaluate_profitability
from app.services.schemas import AllegroResult
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
            run.error_message = "Kategoria nie zostala znaleziona"
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

            market_data = state.last_market_data if state else None

            now = datetime.now(timezone.utc)
            if _should_fetch_from_scraper(
                db_only_mode=db_only_mode,
                market_data=market_data,
                cache_days=cache_days,
                now=now,
            ):
                scraper_request_count += 1
                try:
                    result = asyncio.run(fetch_via_allegro_scraper(item.ean))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("RUN_TASK unexpected error ean=%s", item.ean)
                    result = _error_result(item.ean, f"unexpected:{type(exc).__name__}")

                _apply_scraped_result(db, item, product, category, result)
            else:
                _apply_cached_market_data(item, category, market_data)
                if db_only_mode:
                    db_only_item_count += 1
                else:
                    db_cache_hit_count += 1
                    logger.info("RUN_TASK db_cache hit run_id=%s ean=%s", run.id, item.ean)

            run.processed_products += 1
            _update_run_metadata(
                run,
                db_only_mode=db_only_mode,
                cache_days=cache_days,
                scraper_request_count=scraper_request_count,
                db_cache_hit_count=db_cache_hit_count,
                db_only_item_count=db_only_item_count,
            )
            db.commit()

        if run.status != AnalysisStatus.canceled:
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
    finally:
        db.close()
