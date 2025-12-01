from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, MarketDataSource, ProfitabilityLabel
from app.models.product import Product
from app.models.product_effective_state import ProductEffectiveState
from app.models.product_market_data import ProductMarketData
from app.services.profitability_service import calculate_profitability
from app.services.scraping_service import fetch_allegro_data
from app.utils.excel_reader import ParsedRow, read_input_file


def _stale_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=settings.stale_days)


def start_run(db: Session, category: Category, filename: str) -> AnalysisRun:
    run = AnalysisRun(
        category_id=category.id,
        input_file_name=filename,
        status=AnalysisStatus.pending,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _ensure_product(db: Session, category: Category, row: ParsedRow) -> Product:
    product = (
        db.query(Product)
        .filter(Product.category_id == category.id, Product.ean == row.ean)
        .first()
    )
    if not product:
        product = Product(
            category_id=category.id,
            ean=row.ean,
            name=row.name or row.ean,
            purchase_price=row.purchase_price,
        )
        db.add(product)
        db.flush()
    else:
        product.name = row.name or product.name
        product.purchase_price = row.purchase_price
    return product


def _ensure_effective_state(db: Session, product: Product) -> ProductEffectiveState:
    state = product.effective_state
    if not state:
        state = ProductEffectiveState(product_id=product.id, is_stale=True)
        db.add(state)
        db.flush()
    return state


def _prepare_cached_result(
    state: ProductEffectiveState,
) -> Tuple[Decimal | None, int | None, bool]:
    if not state.last_market_data:
        return None, None, state.is_not_found
    market = state.last_market_data
    return (
        Decimal(market.allegro_price) if market.allegro_price is not None else None,
        market.allegro_sold_count,
        market.is_not_found,
    )


def _persist_market_data(
    db: Session,
    product: Product,
    source: str,
    price: Decimal | None,
    sold_count: int | None,
    is_not_found: bool,
    raw_payload: dict | None,
) -> ProductMarketData:
    source_value = (
        source if source in MarketDataSource._value2member_map_ else MarketDataSource.scraping.value
    )
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


def process_analysis_run(db: Session, run_id: int, mode: str = "mixed") -> None:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        return

    category = db.query(Category).filter(Category.id == run.category_id).first()
    if not category:
        run.status = AnalysisStatus.failed
        run.error_message = "Category not found"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    file_path = Path(settings.upload_dir) / run.input_file_name
    run.status = AnalysisStatus.running
    run.error_message = None
    run.started_at = datetime.now(timezone.utc)
    db.commit()

    try:
        rows = read_input_file(file_path)
    except Exception as exc:
        run.status = AnalysisStatus.failed
        run.error_message = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    run.total_products = len(rows)
    db.commit()

    cutoff = _stale_cutoff()

    for row in rows:
        product = _ensure_product(db, category, row)
        state = _ensure_effective_state(db, product)
        state.is_stale = not state.last_fetched_at or state.last_fetched_at < cutoff

        try:
            if mode == "offline":
                item = _process_offline_row(db, run, category, product, state, row)
            else:
                item = _process_mixed_row(db, run, category, product, state, row, cutoff)
        except Exception as exc:  # capture per-row errors
            item = AnalysisRunItem(
                analysis_run_id=run.id,
                product_id=product.id,
                row_number=row.row_number,
                ean=row.ean,
                input_name=row.name,
                input_purchase_price=row.purchase_price,
                source=AnalysisItemSource.error,
                error_message=str(exc),
            )

        db.add(item)
        run.processed_products += 1
        db.commit()

    run.status = AnalysisStatus.completed
    run.finished_at = datetime.now(timezone.utc)
    db.commit()


def _process_offline_row(
    db: Session,
    run: AnalysisRun,
    category: Category,
    product: Product,
    state: ProductEffectiveState,
    row: ParsedRow,
) -> AnalysisRunItem:
    price, sold_count, is_not_found = _prepare_cached_result(state)
    source = AnalysisItemSource.not_found if is_not_found else AnalysisItemSource.baza

    if is_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
    else:
        score, label = calculate_profitability(row.purchase_price, price, sold_count, category)

    _update_effective_state(state, state.last_market_data, score, label)

    return AnalysisRunItem(
        analysis_run_id=run.id,
        product_id=product.id,
        row_number=row.row_number,
        ean=row.ean,
        input_name=row.name,
        input_purchase_price=row.purchase_price,
        source=source,
        allegro_price=price,
        allegro_sold_count=sold_count,
        profitability_score=score,
        profitability_label=label,
    )


def _process_mixed_row(
    db: Session,
    run: AnalysisRun,
    category: Category,
    product: Product,
    state: ProductEffectiveState,
    row: ParsedRow,
    cutoff: datetime,
) -> AnalysisRunItem:
    use_cached = state.last_fetched_at and not state.is_not_found and state.last_fetched_at >= cutoff
    cached_not_found = state.last_fetched_at and state.is_not_found and state.last_fetched_at >= cutoff

    if cached_not_found:
        score = None
        label = ProfitabilityLabel.nieokreslony
        _update_effective_state(state, state.last_market_data, score, label)
        return AnalysisRunItem(
            analysis_run_id=run.id,
            product_id=product.id,
            row_number=row.row_number,
            ean=row.ean,
            input_name=row.name,
            input_purchase_price=row.purchase_price,
            source=AnalysisItemSource.not_found,
            allegro_price=None,
            allegro_sold_count=None,
            profitability_score=score,
            profitability_label=label,
        )

    if use_cached:
        price, sold_count, _ = _prepare_cached_result(state)
        score, label = calculate_profitability(row.purchase_price, price, sold_count, category)
        _update_effective_state(state, state.last_market_data, score, label)
        return AnalysisRunItem(
            analysis_run_id=run.id,
            product_id=product.id,
            row_number=row.row_number,
            ean=row.ean,
            input_name=row.name,
            input_purchase_price=row.purchase_price,
            source=AnalysisItemSource.baza,
            allegro_price=price,
            allegro_sold_count=sold_count,
            profitability_score=score,
            profitability_label=label,
        )

    result = fetch_allegro_data(row.ean)
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
        source = AnalysisItemSource.not_found
    else:
        score, label = calculate_profitability(row.purchase_price, price, sold_count, category)
        source = AnalysisItemSource.scraping

    _update_effective_state(state, market_data, score, label)

    return AnalysisRunItem(
        analysis_run_id=run.id,
        product_id=product.id,
        row_number=row.row_number,
        ean=row.ean,
        input_name=row.name,
        input_purchase_price=row.purchase_price,
        source=source,
        allegro_price=price,
        allegro_sold_count=sold_count,
        profitability_score=score,
        profitability_label=label,
        error_message=None,
    )


def get_run_status(db: Session, run_id: int) -> AnalysisRun | None:
    return db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()


def get_run_items(db: Session, run_id: int) -> List[AnalysisRunItem]:
    return (
        db.query(AnalysisRunItem)
        .filter(AnalysisRunItem.analysis_run_id == run_id)
        .order_by(AnalysisRunItem.row_number)
        .all()
    )
