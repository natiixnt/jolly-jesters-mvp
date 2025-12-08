from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Tuple

from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, MarketDataSource, ProfitabilityLabel
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
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
    source_value = source if source in MarketDataSource._value2member_map_ else MarketDataSource.scraping.value
    safe_payload = raw_payload
    try:
        if raw_payload is not None:
            json.dumps(raw_payload, default=str)
    except TypeError:
        safe_payload = {"raw": str(raw_payload)}

    md = ProductMarketData(
        product_id=product.id,
        allegro_price=price,
        allegro_sold_count=sold_count,
        source=MarketDataSource(source_value),
        is_not_found=is_not_found,
        raw_payload=safe_payload,
        fetched_at=datetime.now(timezone.utc),
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
            state.is_stale = not state.last_fetched_at or state.last_fetched_at < cutoff

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
    price, sold_count, is_not_found = _prepare_cached_result(state)
    source = AnalysisItemSource.not_found if is_not_found else AnalysisItemSource.baza

    if is_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
    else:
        score, label = calculate_profitability(item.input_purchase_price, price, sold_count, category)

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
    use_cached = state.last_fetched_at and not state.is_not_found and state.last_fetched_at >= cutoff
    cached_not_found = state.last_fetched_at and state.is_not_found and state.last_fetched_at >= cutoff

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
        score, label = calculate_profitability(item.input_purchase_price, price, sold_count, category)
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
        score, label = calculate_profitability(item.input_purchase_price, price, sold_count, category)
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
        score, label = calculate_profitability(item.input_purchase_price, price, sold_count, category)
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
