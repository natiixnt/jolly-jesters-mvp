from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Tuple
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload

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

logger = logging.getLogger(__name__)


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
            if normalized in {"scraping"}:
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
        mode="cached",
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
                scrape_status=ScrapeStatus.pending,
            )
        )

    db.commit()
    db.refresh(run)
    return run


def _prepare_cached_result(state: ProductEffectiveState | None) -> Tuple[Decimal | None, int | None, bool]:
    if not state or not state.last_market_data:
        return None, None, True
    md = state.last_market_data
    return md.allegro_price, md.allegro_sold_count, bool(md.is_not_found)


def _persist_market_data(
    db: Session,
    product: Product,
    source: str,
    price,
    sold_count,
    is_not_found: bool,
    raw_payload: dict | None,
    last_checked_at: datetime | None = None,
) -> ProductMarketData:
    md = ProductMarketData(
        product_id=product.id,
        allegro_price=price,
        allegro_sold_count=sold_count,
        source=MarketDataSource.scraping,
        is_not_found=is_not_found,
        raw_payload=raw_payload,
        last_checked_at=last_checked_at or datetime.now(timezone.utc),
    )
    db.add(md)
    db.flush()
    return md


def _update_effective_state(
    state: ProductEffectiveState,
    market_data: ProductMarketData | None,
    profitability_score: Decimal | None,
    profitability_label: ProfitabilityLabel | None,
) -> None:
    if not state:
        return
    if market_data:
        state.last_market_data_id = market_data.id
        state.last_checked_at = market_data.last_checked_at
        state.is_not_found = market_data.is_not_found
    state.profitability_score = profitability_score
    state.profitability_label = profitability_label
    state.updated_at = datetime.now(timezone.utc)


def list_recent_runs(db: Session, limit: int = 20) -> List[AnalysisRun]:
    rows = (
        db.query(AnalysisRun, Category.name.label("category_name"))
        .join(Category, AnalysisRun.category_id == Category.id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
        .all()
    )
    results: List[AnalysisRun] = []
    for run, cat_name in rows:
        setattr(run, "category_name", cat_name)
        results.append(run)
    return results


def list_active_runs(db: Session, limit: int = 20) -> List[AnalysisRun]:
    rows = (
        db.query(AnalysisRun, Category.name.label("category_name"))
        .join(Category, AnalysisRun.category_id == Category.id)
        .filter(AnalysisRun.status.in_([AnalysisStatus.running, AnalysisStatus.pending]))
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
        .all()
    )
    results: List[AnalysisRun] = []
    for run, cat_name in rows:
        setattr(run, "category_name", cat_name)
        results.append(run)
    return results


def get_latest_run(db: Session) -> AnalysisRun | None:
    return (
        db.query(AnalysisRun)
        .order_by(AnalysisRun.created_at.desc())
        .first()
    )


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


def get_run_results(
    db: Session,
    run_id: int,
    offset: int = 0,
    limit: int = 100,
) -> AnalysisResultsResponse | None:
    run = get_run_status(db, run_id)
    if not run:
        return None

    items = (
        db.query(AnalysisRunItem)
        .filter(AnalysisRunItem.analysis_run_id == run_id)
        .order_by(AnalysisRunItem.row_number)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return AnalysisResultsResponse(
        run_id=run.id,
        status=run.status,
        total=run.total_products,
        error_message=run.error_message,
        items=[_to_result_item(item) for item in items],
    )


def _to_result_item(item: AnalysisRunItem) -> AnalysisResultItem:
    last_checked = None
    if item.product and item.product.effective_state and item.product.effective_state.last_checked_at:
        last_checked = item.product.effective_state.last_checked_at
    return AnalysisResultItem(
        id=item.id,
        row_number=item.row_number,
        ean=item.ean,
        name=item.input_name,
        original_currency=item.original_currency,
        original_purchase_price=float(item.original_purchase_price) if item.original_purchase_price else None,
        purchase_price_pln=float(item.purchase_price_pln) if item.purchase_price_pln else None,
        allegro_price_pln=float(item.allegro_price) if item.allegro_price is not None else None,
        sold_count=item.allegro_sold_count,
        sold_count_status=None,
        margin_pln=None,
        margin_percent=None,
        is_profitable=item.profitability_label == ProfitabilityLabel.oplacalny if item.profitability_label else None,
        source=item.source.value if item.source else None,
        scrape_status=item.scrape_status,
        scrape_error_message=item.error_message,
        last_checked_at=last_checked,
        updated_at=item.updated_at,
    )


def serialize_analysis_item(item: AnalysisRunItem, category: Category | None = None) -> AnalysisResultItem:
    """Stable serializer used by API and Excel export."""
    result = _to_result_item(item)

    purchase = item.purchase_price_pln or item.input_purchase_price
    price = item.allegro_price
    if purchase is not None and price is not None:
        try:
            margin_pln = float(price) - float(purchase)
            result.margin_pln = margin_pln
            if purchase:
                result.margin_percent = (margin_pln / float(purchase)) * 100
        except Exception:
            pass

    # Fallback name: use product name if missing
    if (not result.name or result.name == result.ean) and item.product and item.product.name:
        result.name = item.product.name

    # Preserve profitability flags already set on the item
    if item.profitability_label is not None:
        result.is_profitable = item.profitability_label == ProfitabilityLabel.oplacalny

    return result


def get_run_results_since(
    db: Session,
    run_id: int,
    since: datetime | None = None,
    since_id: int | None = None,
    limit: int = 200,
) -> AnalysisResultsResponse | None:
    run = get_run_status(db, run_id)
    if not run:
        return None

    query = db.query(AnalysisRunItem).filter(AnalysisRunItem.analysis_run_id == run_id)
    if since is not None:
        query = query.filter(or_(AnalysisRunItem.updated_at > since, AnalysisRunItem.updated_at.is_(None)))
    if since_id is not None:
        query = query.filter(AnalysisRunItem.id > since_id)
    query = query.order_by(AnalysisRunItem.id).limit(limit)

    items = query.all()
    if not items:
        return AnalysisResultsResponse(
            run_id=run.id,
            status=run.status,
            total=run.total_products,
            error_message=run.error_message,
            items=[],
            next_since=since,
            next_since_id=since_id,
        )

    next_since_val = max(
        [item.updated_at or item.created_at or datetime.now(timezone.utc) for item in items],
        default=since or datetime.now(timezone.utc),
    )
    next_id = items[-1].id if items else since_id

    return AnalysisResultsResponse(
        run_id=run.id,
        status=run.status,
        total=run.total_products,
        error_message=run.error_message,
        items=[_to_result_item(item) for item in items],
        next_since=next_since_val,
        next_since_id=next_id,
    )


def cancel_analysis_run(db: Session, run_id: int) -> AnalysisRun | None:
    run = get_run_status(db, run_id)
    if not run:
        return None
    if run.status in {AnalysisStatus.completed, AnalysisStatus.failed}:
        return run
    run.status = AnalysisStatus.canceled
    run.canceled_at = datetime.now(timezone.utc)
    db.commit()
    return run


def list_run_task_ids(db: Session, run_id: int) -> List[str]:
    return [
        row.celery_task_id
        for row in db.query(AnalysisRunTask.celery_task_id)
        .filter(AnalysisRunTask.analysis_run_id == run_id)
        .all()
        if row.celery_task_id
    ]


__all__ = [
    "_ensure_product_state",
    "_persist_market_data",
    "_update_effective_state",
    "record_run_task",
    "build_cached_worklist",
    "prepare_cached_analysis_run",
    "list_recent_runs",
    "list_active_runs",
    "get_latest_run",
    "get_run_status",
    "get_run_items",
    "get_run_results",
    "get_run_results_since",
    "cancel_analysis_run",
    "list_run_task_ids",
]
