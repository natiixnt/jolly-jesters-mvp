from __future__ import annotations

import uuid
from pathlib import Path
from typing import List

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.enums import AnalysisItemSource, AnalysisStatus, ScrapeStatus
from app.models.product import Product
from app.services import settings_service
from app.utils.excel_reader import InputRow, read_excel_file


def store_uploaded_file_bytes(data: bytes, original_name: str, upload_dir: Path | None = None) -> Path:
    target_dir = upload_dir or settings.upload_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}_{original_name}"
    filepath = target_dir / filename
    filepath.write_bytes(data)
    return filepath


def _ensure_product(db: Session, category: Category, row: InputRow) -> Product:
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
            purchase_price=row.purchase_price_pln or 0,
        )
        db.add(product)
        db.flush()
    else:
        product.name = row.name or product.name
        if row.purchase_price_pln:
            product.purchase_price = row.purchase_price_pln
    return product


def prepare_analysis_run(
    db: Session,
    category: Category,
    rows: List[InputRow],
    filename: str,
    mode: str = "live",
) -> AnalysisRun:
    run = AnalysisRun(
        category_id=category.id,
        input_file_name=filename,
        status=AnalysisStatus.pending,
        total_products=len(rows),
        processed_products=0,
        mode=mode,
        run_metadata=None,
    )
    db.add(run)
    db.flush()

    invalid_processed = 0

    for row in rows:
        if not row.is_valid:
            db.add(
                AnalysisRunItem(
                    analysis_run_id=run.id,
                    product_id=None,
                    row_number=row.row_number,
                    ean=row.ean,
                    input_name=row.name,
                    original_purchase_price=row.original_purchase_price,
                    original_currency=row.purchase_currency,
                    input_purchase_price=row.purchase_price_pln,
                    purchase_price_pln=row.purchase_price_pln,
                    source=AnalysisItemSource.error,
                    scrape_status=ScrapeStatus.error,
                    error_message=row.error,
                )
            )
            invalid_processed += 1
            continue

        product = _ensure_product(db, category, row)
        db.add(
            AnalysisRunItem(
                analysis_run_id=run.id,
                product_id=product.id,
                row_number=row.row_number,
                ean=row.ean,
                input_name=row.name,
                original_purchase_price=row.original_purchase_price,
                original_currency=row.purchase_currency,
                input_purchase_price=row.purchase_price_pln,
                purchase_price_pln=row.purchase_price_pln,
                source=AnalysisItemSource.baza,
            )
        )

    run.processed_products = invalid_processed
    db.commit()
    db.refresh(run)
    return run


async def handle_upload(
    db: Session,
    category: Category,
    upload_file: UploadFile,
    mode: str = "live",
) -> AnalysisRun:
    data = await upload_file.read()
    rates, default_currency = settings_service.get_currency_rate_map(db)
    rows = read_excel_file(
        data,
        currency_rates=rates,
        default_currency=default_currency,
        file_name=upload_file.filename,
    )
    saved_path = store_uploaded_file_bytes(data, upload_file.filename)
    run = prepare_analysis_run(db, category, rows, saved_path.name, mode=mode)
    return run
