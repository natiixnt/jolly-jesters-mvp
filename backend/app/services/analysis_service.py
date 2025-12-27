from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, MarketDataSource, ProfitabilityLabel
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.schemas.analysis import AnalysisResultItem, AnalysisResultsResponse
from app.services.profitability_service import calculate_profitability
from app.services.schemas import ScrapingStrategyConfig
from app.services.scraping_service import fetch_allegro_data
from app.services import settings_service


def _mark_run_failed(db: Session, run_id: int, message: str) -> None:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
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


def _persist_market_data(
    db: Session,
    product: Product,
    source: str,
    price: Decimal | None,
    sold_count: int | None,
    is_not_found: bool,
    raw_payload: dict | None,
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
    md = ProductMarketData(
        product_id=product.id,
        allegro_price=price,
        allegro_sold_count=sold_count,
        source=MarketDataSource(source_value),
        is_not_found=is_not_found,
        raw_payload=safe_payload,
        fetched_at=now,
        last_checked_at=now,
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


def _fetch_with_strategy(ean: str, strategy: ScrapingStrategyConfig):
    return asyncio.run(fetch_allegro_data(ean, strategy))


def process_analysis_run(db: Session, run_id: int, mode: str = "mixed") -> None:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
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
            use_api=run.use_api,
            use_cloud_http=run.use_cloud_http,
            use_local_scraper=run.use_local_scraper,
        )

        for item in items:
            if item.source == AnalysisItemSource.error:
                continue

            product = (
                db.query(Product)
                .filter(Product.id == item.product_id)
                .first()
            )
            if not product:
                item.source = AnalysisItemSource.error
                item.error_message = "Brak produktu dla wiersza"
                run.processed_products += 1
                db.commit()
                continue

            state = _ensure_product_state(db, product)
            state.is_stale = not state.last_checked_at or state.last_checked_at < cutoff

            try:
                if mode == "offline":
                    _process_offline_item(db, run, category, product, state, item)
                elif mode == "online":
                    _process_online_item(db, run, category, product, state, item, strategy)
                else:
                    _process_mixed_item(db, run, category, product, state, item, cutoff, strategy)
            except Exception as exc:
                item.source = AnalysisItemSource.error
                item.error_message = str(exc)

            run.processed_products += 1
            db.commit()

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
) -> None:
    purchase_price = item.purchase_price_pln or item.input_purchase_price
    use_cached = state.last_checked_at and not state.is_not_found and state.last_checked_at >= cutoff
    cached_not_found = state.last_checked_at and state.is_not_found and state.last_checked_at >= cutoff

    if cached_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
        _update_effective_state(state, state.last_market_data, score, label)
        item.source = AnalysisItemSource.not_found
        item.allegro_price = None
        item.allegro_sold_count = None
        item.profitability_score = score
        item.profitability_label = label
        return

    if use_cached:
        price, sold_count, _ = _prepare_cached_result(state)
        score, label = calculate_profitability(purchase_price, price, sold_count, category)
        _update_effective_state(state, state.last_market_data, score, label)
        item.source = AnalysisItemSource.baza
        item.allegro_price = price
        item.allegro_sold_count = sold_count
        item.profitability_score = score
        item.profitability_label = label
        return

    result = _fetch_with_strategy(item.ean, strategy)
    if result.is_temporary_error:
        item.source = AnalysisItemSource.error
        try:
            item.error_message = json.dumps(result.raw_payload, ensure_ascii=False)
        except TypeError:
            item.error_message = str(result.raw_payload)
        return

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
    )

    if result.is_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
        source_val = AnalysisItemSource.not_found
    else:
        score, label = calculate_profitability(purchase_price, price, sold_count, category)
        source_val = AnalysisItemSource.scraping

    _update_effective_state(state, market_data, score, label)

    item.source = source_val
    item.allegro_price = price
    item.allegro_sold_count = sold_count
    item.profitability_score = score
    item.profitability_label = label
    item.error_message = None


def _process_online_item(
    db: Session,
    run: AnalysisRun,
    category: Category,
    product: Product,
    state: ProductEffectiveState,
    item: AnalysisRunItem,
    strategy: ScrapingStrategyConfig,
) -> None:
    purchase_price = item.purchase_price_pln or item.input_purchase_price
    result = _fetch_with_strategy(item.ean, strategy)
    if result.is_temporary_error:
        item.source = AnalysisItemSource.error
        try:
            item.error_message = json.dumps(result.raw_payload, ensure_ascii=False)
        except TypeError:
            item.error_message = str(result.raw_payload)
        return

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
    )

    if result.is_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
        source_val = AnalysisItemSource.not_found
    else:
        score, label = calculate_profitability(purchase_price, price, sold_count, category)
        source_val = AnalysisItemSource.scraping

    _update_effective_state(state, market_data, score, label)

    item.source = source_val
    item.allegro_price = price
    item.allegro_sold_count = sold_count
    item.profitability_score = score
    item.profitability_label = label
    item.error_message = None


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

    if candidate_source == "local_scraper":
        return "local"
    if candidate_source:
        return str(candidate_source)

    if base_source == AnalysisItemSource.scraping.value:
        return "scraping"
    return base_source


def _resolve_scrape_status(item: AnalysisRunItem) -> str:
    if item.source == AnalysisItemSource.error:
        return "scraper_error"
    if item.source == AnalysisItemSource.not_found:
        return "not_found"
    return "ok"


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
        product = item.product
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

        results.append(
            AnalysisResultItem(
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
        )

    return AnalysisResultsResponse(
        run_id=run.id,
        status=run.status,
        total=total,
        error_message=run.error_message,
        items=results,
    )
