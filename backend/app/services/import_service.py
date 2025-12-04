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
from app.models.enums import AnalysisItemSource, AnalysisStatus
from app.models.product import Product
from app.services.schemas import ScrapingStrategyConfig
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
            purchase_price=row.purchase_price or 0,
        )
        db.add(product)
        db.flush()
    else:
        product.name = row.name or product.name
        if row.purchase_price:
            product.purchase_price = row.purchase_price
    return product


def prepare_analysis_run(
    db: Session,
    category: Category,
    rows: List[InputRow],
    filename: str,
    strategy: ScrapingStrategyConfig,
    mode: str = "mixed",
) -> AnalysisRun:
    run = AnalysisRun(
        category_id=category.id,
        input_file_name=filename,
        status=AnalysisStatus.pending,
        total_products=len(rows),
        processed_products=0,
        mode=mode,
        use_api=strategy.use_api,
        use_cloud_http=strategy.use_cloud_http,
        use_local_scraper=strategy.use_local_scraper,
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
                    input_purchase_price=row.purchase_price,
                    source=AnalysisItemSource.error,
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
                input_purchase_price=row.purchase_price,
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
    strategy: ScrapingStrategyConfig,
    mode: str = "mixed",
) -> AnalysisRun:
    data = await upload_file.read()
    rows = read_excel_file(data)
    saved_path = store_uploaded_file_bytes(data, upload_file.filename)
    run = prepare_analysis_run(db, category, rows, saved_path.name, strategy, mode=mode)
    return run
