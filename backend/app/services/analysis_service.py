from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Iterable, List, Tuple
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.analysis_run_task import AnalysisRunTask
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, MarketDataSource, ProfitabilityLabel, ScrapeStatus
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.schemas.analysis import AnalysisResultItem, AnalysisResultsResponse
from app.services.profitability_service import calculate_profitability
from app.services.schemas import ScrapingStrategyConfig
from app.services import settings_service

logger = logging.getLogger(__name__)


def _mark_run_failed(db: Session, run_id: int, message: str) -> None:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        return
    if run.status == AnalysisStatus.canceled:
        return
    run.status = AnalysisStatus.failed
    run.error_message = message
    run.finished_at = datetime.now(timezone.utc)
    db.commit()


def _stale_cutoff(cache_ttl_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=cache_ttl_days)


def _ensure_product_state(db: Session, product: Product) -> ProductEffectiveState:
    state = product.effective_state
    if not state:
        state = ProductEffectiveState(product_id=product.id, is_stale=True)
        db.add(state)
        db.flush()
    return state


def record_run_task(
    db: Session,
    run: AnalysisRun,
    task_id: str,
    kind: str,
    item: AnalysisRunItem | None = None,
    ean: str | None = None,
) -> None:
    if not task_id:
        return
    db.add(
        AnalysisRunTask(
            analysis_run_id=run.id,
            analysis_run_item_id=item.id if item else None,
            celery_task_id=task_id,
            kind=kind,
            ean=ean,
        )
    )


def _resolve_cached_cutoff(cache_days: int | None, include_all_cached: bool) -> datetime | None:
    if include_all_cached:
        return None
    days = cache_days if cache_days is not None and cache_days > 0 else 30
    return datetime.now(timezone.utc) - timedelta(days=days)


def build_cached_worklist(
    db: Session,
    category_id: UUID,
    cache_days: int | None = 30,
    include_all_cached: bool = False,
    only_with_data: bool = False,
    limit: int | None = None,
    source: str | None = None,
    ean_contains: str | None = None,
) -> List[Product]:
    base = (
        db.query(Product)
        .outerjoin(ProductEffectiveState, ProductEffectiveState.product_id == Product.id)
        .outerjoin(ProductMarketData, ProductMarketData.id == ProductEffectiveState.last_market_data_id)
        .filter(Product.category_id == category_id)
    )

    total_candidates = base.count()
    cutoff = _resolve_cached_cutoff(cache_days, include_all_cached)

    if cutoff is not None:
        effective_ts = func.coalesce(
            ProductMarketData.last_checked_at,
            ProductEffectiveState.last_checked_at,
            Product.updated_at,
            Product.created_at,
        )
        base = base.filter(effective_ts >= cutoff)

    if only_with_data:
        base = base.filter(
            ProductMarketData.id.isnot(None),
            ProductMarketData.is_not_found.is_(False),
        )

    if source:
        normalized = source.strip().lower()
        if normalized not in {"any", "all"}:
            if normalized in {"local_scraper", "local"}:
                base = base.filter(ProductMarketData.source == MarketDataSource.local)
            elif normalized in {"cloud", "cloud_http"}:
                base = base.filter(ProductMarketData.source == MarketDataSource.cloud_http)
            elif normalized in {"scraping"}:
                base = base.filter(ProductMarketData.source == MarketDataSource.scraping)
            elif normalized in {"api"}:
                base = base.filter(ProductMarketData.source == MarketDataSource.api)
            else:
                raise ValueError("Invalid source filter")

    if ean_contains and ean_contains.strip():
        base = base.filter(Product.ean.ilike(f"%{ean_contains.strip()}%"))

    filtered_count = base.count()

    base = base.order_by(
        func.coalesce(ProductMarketData.last_checked_at, Product.updated_at).desc().nullslast(),
        Product.created_at.desc(),
    )

    if limit is not None and limit > 0:
        base = base.limit(limit)

    products = base.all()
    deduped: List[Product] = []
    seen: set[str] = set()
    for product in products:
        if product.ean in seen:
            continue
        seen.add(product.ean)
        deduped.append(product)

    logger.info(
        "CACHED_WORKLIST category=%s candidates=%s filtered=%s final=%s include_all=%s only_with_data=%s cache_days=%s limit=%s source=%s ean=%s",
        category_id,
        total_candidates,
        filtered_count,
        len(deduped),
        include_all_cached,
        only_with_data,
        cache_days,
        limit,
        source,
        ean_contains,
    )
    return deduped


def prepare_cached_analysis_run(
    db: Session,
    category: Category,
    products: List[Product],
    strategy: ScrapingStrategyConfig,
    mode: str = "mixed",
    run_metadata: dict | None = None,
) -> AnalysisRun:
    run = AnalysisRun(
        category_id=category.id,
        input_file_name="cached_db",
        input_source="cache",
        run_metadata=run_metadata or {},
        status=AnalysisStatus.pending,
        total_products=len(products),
        processed_products=0,
        mode=mode,
        use_cloud_http=strategy.use_cloud_http,
        use_local_scraper=strategy.use_local_scraper,
    )
    db.add(run)
    db.flush()

    for idx, product in enumerate(products, start=1):
        purchase_price = product.purchase_price
        db.add(
            AnalysisRunItem(
                analysis_run_id=run.id,
                product_id=product.id,
                row_number=idx,
                ean=product.ean,
                input_name=product.name,
                original_purchase_price=purchase_price,
                original_currency="PLN",
                input_purchase_price=purchase_price,
                purchase_price_pln=purchase_price,
                source=AnalysisItemSource.baza,
            )
        )

    db.commit()
    db.refresh(run)
    return run


def list_run_task_ids(db: Session, run_id: int) -> List[str]:
    rows = (
        db.query(AnalysisRunTask.celery_task_id)
        .filter(AnalysisRunTask.analysis_run_id == run_id)
        .all()
    )
    return [row[0] for row in rows if row and row[0]]


def cancel_analysis_run(
    db: Session,
    run_id: int,
    reason: str | None = None,
) -> AnalysisRun | None:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        return None
    if run.status == AnalysisStatus.canceled:
        return run
    if run.status in {AnalysisStatus.completed, AnalysisStatus.failed}:
        return run

    now = datetime.now(timezone.utc)
    run.status = AnalysisStatus.canceled
    run.canceled_at = now
    run.finished_at = run.finished_at or now
    run.error_message = reason or "Anulowano przez użytkownika"
    db.commit()
    return run


def retry_failed_items(
    db: Session,
    run_id: int,
    enqueue: Callable[[AnalysisRunItem], str | None],
    retry_statuses: set[ScrapeStatus] | None = None,
) -> int:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run or run.status == AnalysisStatus.canceled:
        return 0

    statuses = retry_statuses or {ScrapeStatus.error, ScrapeStatus.network_error, ScrapeStatus.blocked}
    items = (
        db.query(AnalysisRunItem)
        .filter(
            AnalysisRunItem.analysis_run_id == run_id,
            AnalysisRunItem.scrape_status.in_(statuses),
        )
        .all()
    )
    if not items:
        return 0

    if run.status in {AnalysisStatus.completed, AnalysisStatus.failed}:
        run.status = AnalysisStatus.running
        run.finished_at = None
    run.error_message = None

    scheduled = 0
    for item in items:
        item.scrape_status = ScrapeStatus.pending
        item.error_message = None
        item.source = AnalysisItemSource.scraping
        task_id = enqueue(item)
        if task_id:
            record_run_task(db, run, task_id, "retry", item=item, ean=item.ean)
        scheduled += 1

    db.commit()
    return scheduled


def _persist_market_data(
    db: Session,
    product: Product,
    source: str,
    price: Decimal | None,
    sold_count: int | None,
    is_not_found: bool,
    raw_payload: dict | None,
    last_checked_at: datetime | None = None,
) -> ProductMarketData:
    if source == "local_scraper":
        source_value = "local"
    else:
        source_value = source if source in MarketDataSource._value2member_map_ else MarketDataSource.scraping.value
    safe_payload = raw_payload
    try:
        if raw_payload is not None:
            json.dumps(raw_payload, default=str)
    except TypeError:
        safe_payload = {"raw": str(raw_payload)}

    now = datetime.now(timezone.utc)
    effective_checked = last_checked_at or now
    if effective_checked and effective_checked.tzinfo is None:
        effective_checked = effective_checked.replace(tzinfo=timezone.utc)

    md = ProductMarketData(
        product_id=product.id,
        allegro_price=price,
        allegro_sold_count=sold_count,
        source=MarketDataSource(source_value),
        is_not_found=is_not_found,
        raw_payload=safe_payload,
        fetched_at=now,
        last_checked_at=effective_checked,
    )
    db.add(md)
    db.flush()
    return md


def _update_effective_state(
    state: ProductEffectiveState,
    market_data: ProductMarketData | None,
    score: Decimal | None,
    label: ProfitabilityLabel,
) -> None:
    now = datetime.now(timezone.utc)
    if market_data:
        state.last_market_data_id = market_data.id
        state.last_market_data = market_data
        state.last_fetched_at = market_data.fetched_at or now
        state.last_checked_at = market_data.last_checked_at or market_data.fetched_at or now
        state.is_not_found = market_data.is_not_found
        state.is_stale = False
    state.profitability_score = score
    state.profitability_label = label
    state.last_analysis_at = now


def _prepare_cached_result(state: ProductEffectiveState) -> Tuple[Decimal | None, int | None, bool]:
    if not state.last_market_data:
        return None, None, state.is_not_found
    market = state.last_market_data
    return (
        Decimal(market.allegro_price) if market.allegro_price is not None else None,
        market.allegro_sold_count,
        market.is_not_found,
    )


TERMINAL_STATUSES = {
    ScrapeStatus.ok,
    ScrapeStatus.not_found,
    ScrapeStatus.blocked,
    ScrapeStatus.network_error,
    ScrapeStatus.error,
}


def _normalize_scrape_status(item: AnalysisRunItem) -> ScrapeStatus | None:
    status = item.scrape_status
    if status is not None:
        return status
    if item.source == AnalysisItemSource.not_found:
        item.scrape_status = ScrapeStatus.not_found
    elif item.source == AnalysisItemSource.error:
        item.scrape_status = ScrapeStatus.error
    else:
        item.scrape_status = ScrapeStatus.ok
    return item.scrape_status


def _set_scrape_status(
    item: AnalysisRunItem,
    status: ScrapeStatus,
    error_message: str | None = None,
) -> None:
    item.scrape_status = status
    item.error_message = error_message


def _enqueue_scrape(
    item: AnalysisRunItem,
    strategy: ScrapingStrategyConfig,
    enqueue_cloud_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None,
    enqueue_local_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None,
) -> bool:
    if strategy.use_cloud_http and enqueue_cloud_scrape and settings.proxy_list:
        item.source = AnalysisItemSource.scraping
        _set_scrape_status(item, ScrapeStatus.in_progress, None)
        enqueue_cloud_scrape(item, strategy)
        return False

    if (
        strategy.use_local_scraper
        and enqueue_local_scrape
        and settings.LOCAL_SCRAPER_ENABLED
        and settings.LOCAL_SCRAPER_URL
    ):
        item.source = AnalysisItemSource.scraping
        _set_scrape_status(item, ScrapeStatus.pending, None)
        enqueue_local_scrape(item, strategy)
        return False

    item.source = AnalysisItemSource.error
    _set_scrape_status(item, ScrapeStatus.error, "no_scraper_strategy")
    return True


def _should_rescrape_cached_not_found(strategy: ScrapingStrategyConfig) -> bool:
    if not strategy.use_local_scraper:
        return False
    return bool(settings.LOCAL_SCRAPER_ENABLED and settings.LOCAL_SCRAPER_URL)


def _iter_batches(items: List[AnalysisRunItem], batch_size: int) -> Iterable[List[AnalysisRunItem]]:
    if batch_size <= 0:
        yield items
        return
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]


def process_analysis_run(
    db: Session,
    run_id: int,
    mode: str = "mixed",
    enqueue_cloud_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None = None,
    enqueue_local_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None = None,
) -> None:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        return
    if run.status == AnalysisStatus.canceled:
        logger.info("ANALYSIS_RUN canceled_before_start run_id=%s", run_id)
        return

    try:
        category = db.query(Category).filter(Category.id == run.category_id).first()
        if not category:
            run.status = AnalysisStatus.failed
            run.error_message = "Kategoria nie została znaleziona"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        items: List[AnalysisRunItem] = (
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

        settings_record = settings_service.get_settings(db)
        cutoff = _stale_cutoff(settings_record.cache_ttl_days)
        strategy = ScrapingStrategyConfig(
            use_cloud_http=run.use_cloud_http,
            use_local_scraper=run.use_local_scraper,
        )

        batch_size = 10 if strategy.use_local_scraper else 0
        pending_items = 0

        for batch in _iter_batches(items, batch_size):
            db.refresh(run)
            if run.status == AnalysisStatus.canceled:
                logger.info("ANALYSIS_RUN canceled_midway run_id=%s", run_id)
                return
            for item in batch:
                db.refresh(run)
                if run.status == AnalysisStatus.canceled:
                    logger.info("ANALYSIS_RUN canceled_midway run_id=%s", run_id)
                    return
                status = _normalize_scrape_status(item)
                if status in TERMINAL_STATUSES:
                    continue

                product = (
                    db.query(Product)
                    .filter(Product.id == item.product_id)
                    .first()
                )
                if not product:
                    item.source = AnalysisItemSource.error
                    _set_scrape_status(item, ScrapeStatus.error, "Brak produktu dla wiersza")
                    run.processed_products += 1
                    db.commit()
                    continue

                state = _ensure_product_state(db, product)
                state.is_stale = not state.last_checked_at or state.last_checked_at < cutoff

                try:
                    if mode == "offline":
                        _process_offline_item(db, run, category, product, state, item)
                        status = (
                            ScrapeStatus.not_found
                            if item.source == AnalysisItemSource.not_found
                            else ScrapeStatus.ok
                        )
                        _set_scrape_status(item, status, None)
                        run.processed_products += 1
                    elif mode == "online":
                        processed = _process_online_item(
                            db,
                            run,
                            category,
                            product,
                            state,
                            item,
                            strategy,
                            enqueue_cloud_scrape=enqueue_cloud_scrape,
                            enqueue_local_scrape=enqueue_local_scrape,
                        )
                        if processed:
                            run.processed_products += 1
                    else:
                        processed = _process_mixed_item(
                            db,
                            run,
                            category,
                            product,
                            state,
                            item,
                            cutoff,
                            strategy,
                            enqueue_cloud_scrape=enqueue_cloud_scrape,
                            enqueue_local_scrape=enqueue_local_scrape,
                        )
                        if processed:
                            run.processed_products += 1
                except Exception as exc:
                    item.source = AnalysisItemSource.error
                    _set_scrape_status(item, ScrapeStatus.error, str(exc))
                    run.processed_products += 1

                if item.scrape_status in {ScrapeStatus.pending, ScrapeStatus.in_progress}:
                    pending_items += 1
                db.commit()

        if run.status != AnalysisStatus.canceled and pending_items == 0 and run.processed_products >= run.total_products:
            run.status = AnalysisStatus.completed
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        db.rollback()
        _mark_run_failed(db, run_id, f"Analiza przerwana: {exc}")


def _process_offline_item(
    db: Session,
    run: AnalysisRun,
    category: Category,
    product: Product,
    state: ProductEffectiveState,
    item: AnalysisRunItem,
) -> None:
    purchase_price = item.purchase_price_pln or item.input_purchase_price
    price, sold_count, is_not_found = _prepare_cached_result(state)
    source = AnalysisItemSource.not_found if is_not_found else AnalysisItemSource.baza

    if is_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
    else:
        score, label = calculate_profitability(purchase_price, price, sold_count, category)

    _update_effective_state(state, state.last_market_data, score, label)

    item.source = source
    item.allegro_price = price
    item.allegro_sold_count = sold_count
    item.profitability_score = score
    item.profitability_label = label


def _process_mixed_item(
    db: Session,
    run: AnalysisRun,
    category: Category,
    product: Product,
    state: ProductEffectiveState,
    item: AnalysisRunItem,
    cutoff: datetime,
    strategy: ScrapingStrategyConfig,
    enqueue_cloud_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None = None,
    enqueue_local_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None = None,
) -> bool:
    purchase_price = item.purchase_price_pln or item.input_purchase_price
    use_cached = state.last_checked_at and not state.is_not_found and state.last_checked_at >= cutoff
    cached_not_found = state.last_checked_at and state.is_not_found and state.last_checked_at >= cutoff

    if cached_not_found:
        if _should_rescrape_cached_not_found(strategy):
            return _enqueue_scrape(item, strategy, enqueue_cloud_scrape, enqueue_local_scrape)
        score = None
        label = ProfitabilityLabel.nieokreslony
        _update_effective_state(state, state.last_market_data, score, label)
        item.source = AnalysisItemSource.not_found
        item.allegro_price = None
        item.allegro_sold_count = None
        item.profitability_score = score
        item.profitability_label = label
        _set_scrape_status(item, ScrapeStatus.not_found, None)
        return True

    if use_cached:
        price, sold_count, _ = _prepare_cached_result(state)
        score, label = calculate_profitability(purchase_price, price, sold_count, category)
        _update_effective_state(state, state.last_market_data, score, label)
        item.source = AnalysisItemSource.baza
        item.allegro_price = price
        item.allegro_sold_count = sold_count
        item.profitability_score = score
        item.profitability_label = label
        _set_scrape_status(item, ScrapeStatus.ok, None)
        return True
    return _enqueue_scrape(item, strategy, enqueue_cloud_scrape, enqueue_local_scrape)


def _process_online_item(
    db: Session,
    run: AnalysisRun,
    category: Category,
    product: Product,
    state: ProductEffectiveState,
    item: AnalysisRunItem,
    strategy: ScrapingStrategyConfig,
    enqueue_cloud_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None = None,
    enqueue_local_scrape: Callable[[AnalysisRunItem, ScrapingStrategyConfig], None] | None = None,
) -> bool:
    return _enqueue_scrape(item, strategy, enqueue_cloud_scrape, enqueue_local_scrape)


def get_run_status(db: Session, run_id: int) -> AnalysisRun | None:
    return db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()


def get_run_items(db: Session, run_id: int) -> List[AnalysisRunItem]:
    return (
        db.query(AnalysisRunItem)
        .options(
            selectinload(AnalysisRunItem.product)
            .selectinload(Product.effective_state)
            .selectinload(ProductEffectiveState.last_market_data)
        )
        .filter(AnalysisRunItem.analysis_run_id == run_id)
        .order_by(AnalysisRunItem.row_number)
        .all()
    )


def list_recent_runs(db: Session, limit: int = 20) -> List[AnalysisRun]:
    rows = (
        db.query(AnalysisRun, Category.name.label("category_name"))
        .outerjoin(Category, AnalysisRun.category_id == Category.id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
        .all()
    )
    runs: List[AnalysisRun] = []
    for run, category_name in rows:
        run.category_name = category_name or "Kategoria usunięta"  # type: ignore[attr-defined]
        runs.append(run)
    return runs


def list_active_runs(db: Session, limit: int = 20) -> List[AnalysisRun]:
    rows = (
        db.query(AnalysisRun, Category.name.label("category_name"))
        .outerjoin(Category, AnalysisRun.category_id == Category.id)
        .filter(AnalysisRun.status.in_([AnalysisStatus.pending, AnalysisStatus.running]))
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
        .all()
    )
    runs: List[AnalysisRun] = []
    for run, category_name in rows:
        run.category_name = category_name or "Kategoria usunięta"  # type: ignore[attr-defined]
        runs.append(run)
    return runs


def _compute_margins(
    purchase_price_pln: Decimal | None,
    allegro_price: Decimal | None,
    commission_rate: Decimal,
) -> tuple[Decimal | None, Decimal | None]:
    if purchase_price_pln is None or allegro_price is None:
        return None, None
    try:
        purchase = Decimal(purchase_price_pln)
        sale = Decimal(allegro_price)
    except Exception:
        return None, None

    net_revenue = sale * (Decimal("1") - commission_rate)
    margin = net_revenue - purchase
    if purchase == 0:
        return margin, None
    return margin, (margin / purchase) * Decimal(100)


def _resolve_source_label(item: AnalysisRunItem, product: Product | None) -> str | None:
    base_source = getattr(item.source, "value", item.source)
    if base_source == AnalysisItemSource.baza.value:
        return "cache"
    if base_source == AnalysisItemSource.not_found.value:
        return "not_found"
    if base_source == AnalysisItemSource.error.value:
        return "error"

    candidate_source = None
    if product and product.effective_state and product.effective_state.last_market_data:
        md = product.effective_state.last_market_data
        try:
            candidate_source = (md.raw_payload or {}).get("source")
        except Exception:
            candidate_source = None
        if not candidate_source and md.source:
            candidate_source = getattr(md.source, "value", md.source)
    if candidate_source:
        return str(candidate_source)

    if base_source == AnalysisItemSource.scraping.value:
        return "scraping"
    return base_source


def _resolve_scrape_status(item: AnalysisRunItem) -> ScrapeStatus:
    if item.scrape_status is not None:
        return item.scrape_status
    if item.source == AnalysisItemSource.error:
        return ScrapeStatus.error
    if item.source == AnalysisItemSource.not_found:
        return ScrapeStatus.not_found
    return ScrapeStatus.ok


def serialize_analysis_item(
    item: AnalysisRunItem,
    category: Category | None,
    commission_rate: Decimal | None = None,
) -> AnalysisResultItem:
    product = item.product
    if commission_rate is None:
        commission_rate = Decimal(category.commission_rate or 0) if category else Decimal(0)

    purchase_price_pln = (
        item.purchase_price_pln
        if item.purchase_price_pln is not None
        else (
            item.input_purchase_price
            if item.input_purchase_price is not None
            else (product.purchase_price if product else None)
        )
    )
    original_purchase_price = item.original_purchase_price
    name = item.input_name or (product.name if product else None)
    margin_pln, margin_percent = _compute_margins(purchase_price_pln, item.allegro_price, commission_rate)
    profitability_label = getattr(item.profitability_label, "value", item.profitability_label)
    is_profitable = None
    if profitability_label == ProfitabilityLabel.oplacalny.value:
        is_profitable = True
    elif profitability_label == ProfitabilityLabel.nieoplacalny.value:
        is_profitable = False

    state = product.effective_state if product else None
    last_checked_at = None
    if state:
        last_checked_at = (
            state.last_analysis_at
            or state.last_checked_at
            or (state.last_market_data.last_checked_at if state.last_market_data else None)
        )

    return AnalysisResultItem(
        id=item.id,
        ean=item.ean,
        name=name,
        original_currency=item.original_currency,
        original_purchase_price=float(original_purchase_price) if original_purchase_price is not None else None,
        purchase_price_pln=float(purchase_price_pln) if purchase_price_pln is not None else None,
        allegro_price_pln=float(item.allegro_price) if item.allegro_price is not None else None,
        sold_count=item.allegro_sold_count,
        margin_pln=float(margin_pln) if margin_pln is not None else None,
        margin_percent=float(margin_percent) if margin_percent is not None else None,
        is_profitable=is_profitable,
        source=_resolve_source_label(item, product),
        scrape_status=_resolve_scrape_status(item),
        scrape_error_message=item.error_message,
        last_checked_at=last_checked_at,
    )


def get_run_results(db: Session, run_id: int, offset: int = 0, limit: int = 100) -> AnalysisResultsResponse | None:
    run = (
        db.query(AnalysisRun)
        .options(selectinload(AnalysisRun.category))
        .filter(AnalysisRun.id == run_id)
        .first()
    )
    if not run:
        return None

    total = (
        db.query(func.count(AnalysisRunItem.id))
        .filter(AnalysisRunItem.analysis_run_id == run_id)
        .scalar()
        or 0
    )

    items = (
        db.query(AnalysisRunItem)
        .options(
            selectinload(AnalysisRunItem.product)
            .selectinload(Product.effective_state)
            .selectinload(ProductEffectiveState.last_market_data)
        )
        .filter(AnalysisRunItem.analysis_run_id == run_id)
        .order_by(AnalysisRunItem.row_number)
        .offset(max(0, offset))
        .limit(max(1, min(limit, 500)))
        .all()
    )

    commission_rate = Decimal(run.category.commission_rate or 0) if run.category else Decimal(0)
    results: list[AnalysisResultItem] = []
    for item in items:
        results.append(serialize_analysis_item(item, run.category, commission_rate))

    return AnalysisResultsResponse(
        run_id=run.id,
        status=run.status,
        total=total,
        error_message=run.error_message,
        items=results,
    )
