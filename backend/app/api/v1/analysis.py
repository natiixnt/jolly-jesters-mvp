from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.db.session import SessionLocal, get_db
from app.models.category import Category
from app.models.enums import AnalysisStatus, ScrapeStatus
from app.schemas.analysis import (
    AnalysisResultsResponse,
    AnalysisRunListResponse,
    AnalysisRunMetrics,
    AnalysisRunSummary,
    AnalysisStartFromDbRequest,
    AnalysisStatusResponse,
    AnalysisUploadResponse,
)
import csv
import io

from app.api.deps import CurrentUser, get_current_user_optional, tenant_filter
from app.core.config import settings as app_settings
from app.models.analysis_run import AnalysisRun
from app.services import analysis_service
from app.services.audit_service import log_event
from app.services.export_service import export_run_bytes
from app.services.import_service import handle_upload
from app.utils.validators import validate_ean, sanitize_string
from app.workers.tasks import celery_app, run_analysis_task


def _check_concurrent_limit(db: Session, current_user: Optional[CurrentUser] = None) -> None:
    """Check both per-user and global concurrency limits."""
    from app.core.config import get_settings
    settings = get_settings()

    # Global limit
    all_active = analysis_service.list_active_runs(db, limit=settings.concurrency_global_max + 1)
    if len(all_active) >= settings.concurrency_global_max:
        raise HTTPException(
            status_code=429,
            detail=f"Globalny limit rownoczesnych analiz ({settings.concurrency_global_max}) osiagniety. Sprobuj pozniej."
        )

    # Per-user limit (if user is authenticated)
    if current_user:
        user_active = [r for r in all_active if r.user_id and str(r.user_id) == str(current_user.user_id)]
        if len(user_active) >= settings.concurrency_per_user:
            raise HTTPException(
                status_code=429,
                detail=f"Limit rownoczesnych analiz na uzytkownika ({settings.concurrency_per_user}) osiagniety."
            )

router = APIRouter(tags=["analysis"])

STREAM_POLL_INTERVAL = 2.0


def _verify_run_access(run, current_user: Optional[CurrentUser]) -> None:
    """Raise 404 if run doesn't belong to tenant (when multi-tenant active)."""
    if run and run.tenant_id:
        if not current_user or current_user.tenant_id != run.tenant_id:
            raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
STREAM_HEARTBEAT_INTERVAL = 5.0


def _sse_event(event_type: str, payload: dict) -> str:
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


@router.post("/upload", response_model=AnalysisUploadResponse)
@limiter.limit("10/minute")
async def upload_analysis(
    request: Request,
    file: UploadFile = File(...),
    category_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Nieprawidlowy identyfikator kategorii")

    category = db.query(Category).filter(Category.id == category_uuid).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Kategoria nie znaleziona lub nieaktywna")

    if not file.filename or not file.filename.lower().endswith((".xls", ".xlsx", ".csv")):
        raise HTTPException(status_code=400, detail="Plik musi byc .xls/.xlsx/.csv")

    _check_concurrent_limit(db, current_user)

    # enforce max upload size (50 MB) - read in chunks to avoid unbounded memory use
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    chunks = []
    total_size = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB chunks
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"Plik za duzy (max {MAX_UPLOAD_BYTES // (1024*1024)} MB)")
        chunks.append(chunk)
    content = b"".join(chunks)

    # Validate magic bytes match declared file type
    fname_lower = file.filename.lower()
    if fname_lower.endswith((".xlsx",)):
        # XLSX files are ZIP archives - magic bytes PK\x03\x04
        if not content[:4].startswith(b"PK"):
            raise HTTPException(status_code=400, detail="Zawartosc pliku nie odpowiada formatowi XLSX")
    elif fname_lower.endswith((".xls",)):
        # XLS files - OLE2 magic bytes
        if not content[:8].startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise HTTPException(status_code=400, detail="Zawartosc pliku nie odpowiada formatowi XLS")
    # CSV has no magic bytes - validated during parsing

    await file.seek(0)

    run = await handle_upload(db, category, file)
    if current_user:
        run.tenant_id = current_user.tenant_id
        run.user_id = current_user.user_id
    try:
        result = run_analysis_task.delay(run.id)
        run.root_task_id = result.id
        analysis_service.record_run_task(db, run, result.id, "run_analysis")
        db.commit()
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).error("Failed to enqueue task for run %s: %s", run.id, exc)
        run.status = AnalysisStatus.failed
        run.error_message = "Nie udalo sie uruchomic zadania"
        db.commit()
        raise HTTPException(status_code=503, detail="Nie udalo sie uruchomic analizy")

    log_event("file_upload",
              user_id=str(current_user.user_id) if current_user else None,
              tenant_id=str(current_user.tenant_id) if current_user else None,
              ip=request.client.host if request.client else None,
              details={"filename": file.filename, "run_id": run.id, "category_id": category_id})

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)


@router.post("/run_from_db", response_model=AnalysisUploadResponse)
def run_from_db(
    payload: AnalysisStartFromDbRequest,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    return _start_cached_analysis(payload, db, current_user=current_user)


from pydantic import BaseModel as PydanticBaseModel, validator as pydantic_validator


class BulkEanRequest(PydanticBaseModel):
    category_id: UUID
    items: list[dict]

    @pydantic_validator("items")
    def validate_items_length(cls, v):
        if len(v) == 0:
            raise ValueError("Items list must not be empty")
        if len(v) > 10000:
            raise ValueError("Items list must have at most 10000 entries")
        return v


@router.post("/bulk", response_model=AnalysisUploadResponse)
def bulk_ean_analysis(
    body: BulkEanRequest,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    """Start analysis from JSON list of EANs (no file upload needed)."""
    from app.models.analysis_run_item import AnalysisRunItem
    from app.models.product import Product
    from app.models.enums import AnalysisItemSource, ScrapeStatus as SS

    if not body.items or len(body.items) > 10000:
        raise HTTPException(status_code=400, detail="Lista produktow musi zawierac od 1 do 10000 elementow")

    cat = db.query(Category).filter(Category.id == body.category_id).first()
    if not cat or not cat.is_active:
        raise HTTPException(status_code=404, detail="Kategoria nie znaleziona lub nieaktywna")

    _check_concurrent_limit(db, current_user)

    run = AnalysisRun(
        category_id=cat.id,
        input_file_name="bulk_api",
        input_source="api",
        run_metadata={"source": "bulk_api", "item_count": len(body.items)},
        status=AnalysisStatus.pending,
        total_products=len(body.items),
        processed_products=0,
        mode="live",
    )
    if current_user:
        run.tenant_id = current_user.tenant_id
        run.user_id = current_user.user_id
    db.add(run)
    db.flush()

    invalid_eans = []
    for idx, entry in enumerate(body.items, start=1):
        ean = str(entry.get("ean", "")).strip()
        if not ean:
            continue
        try:
            ean = validate_ean(ean)
        except ValueError:
            invalid_eans.append(ean)
            continue
        price = entry.get("purchase_price") or entry.get("price")
        name = sanitize_string(str(entry.get("name", "")), max_length=500)

        product = db.query(Product).filter(Product.ean == ean, Product.category_id == cat.id).first()
        if not product:
            product = Product(ean=ean, name=name or ean, category_id=cat.id, purchase_price=price)
            db.add(product)
            db.flush()

        db.add(AnalysisRunItem(
            analysis_run_id=run.id,
            product_id=product.id,
            row_number=idx,
            ean=ean,
            input_name=name,
            original_purchase_price=price,
            original_currency="PLN",
            input_purchase_price=price,
            purchase_price_pln=price,
            source=AnalysisItemSource.baza,
            scrape_status=SS.pending,
        ))

    db.commit()
    db.refresh(run)

    try:
        result = run_analysis_task.delay(run.id)
        run.root_task_id = result.id
        analysis_service.record_run_task(db, run, result.id, "run_analysis")
        db.commit()
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).error("Failed to enqueue task for run %s: %s", run.id, exc)
        run.status = AnalysisStatus.failed
        run.error_message = "Nie udalo sie uruchomic zadania"
        db.commit()
        raise HTTPException(status_code=503, detail="Nie udalo sie uruchomic analizy")

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)


@router.get("", response_model=list[AnalysisRunSummary])
def list_runs(
    db: Session = Depends(get_db),
    limit: int = 20,
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    runs = analysis_service.list_recent_runs(db, limit=max(1, min(limit, 200)), tenant_id=current_user.tenant_id if current_user else None)
    return runs


@router.get("/active", response_model=AnalysisRunListResponse)
def list_active_runs(
    db: Session = Depends(get_db),
    limit: int = 20,
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    runs = analysis_service.list_active_runs(db, limit=max(1, min(limit, 200)), tenant_id=current_user.tenant_id if current_user else None)
    return {"runs": runs}


@router.get("/latest", response_model=AnalysisStatusResponse)
def latest_run(
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    run = analysis_service.get_latest_run(db, tenant_id=current_user.tenant_id if current_user else None)
    if not run:
        raise HTTPException(status_code=404, detail="Brak uruchomień")
    return run


@router.get("/{run_id}/metrics", response_model=AnalysisRunMetrics)
def get_run_metrics(run_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    run = analysis_service.get_run_status(db, run_id)
    _verify_run_access(run, current_user)
    metrics = analysis_service.get_run_metrics(db, run_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    return metrics


@router.get("/{run_id}/metrics/csv")
def export_metrics_csv(run_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    run = analysis_service.get_run_status(db, run_id)
    _verify_run_access(run, current_user)
    metrics = analysis_service.get_run_metrics(db, run_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    output = io.StringIO()
    writer = csv.writer(output)
    fields = metrics.dict()
    writer.writerow(fields.keys())
    writer.writerow(fields.values())
    content = output.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="metrics_run_{run_id}.csv"'},
    )


@router.get("/{run_id}/metrics/excel")
def export_metrics_excel(run_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    """Export run metrics as XLSX file."""
    run = analysis_service.get_run_status(db, run_id)
    _verify_run_access(run, current_user)
    if not run:
        raise HTTPException(status_code=404, detail="Nie znaleziono uruchomienia")

    metrics = analysis_service.get_run_metrics(db, run_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Metryki runu"

    headers = ["Metryka", "Wartosc"]
    ws.append(headers)

    rows = [
        ("ID runu", metrics.run_id),
        ("Produkty ogolnie", metrics.total_items),
        ("Zakonczone", metrics.completed_items),
        ("Bledy", metrics.failed_items),
        ("Nie znalezione", metrics.not_found_items),
        ("Zablokowane", metrics.blocked_items),
        ("EAN/min", metrics.ean_per_min),
        ("Koszt/1000 EAN (est.)", metrics.cost_per_1000_ean),
        ("Success rate", metrics.success_rate),
        ("Retry rate", metrics.retry_rate),
        ("CAPTCHA rate", metrics.captcha_rate),
        ("Blocked rate", metrics.blocked_rate),
        ("Network error rate", metrics.network_error_rate),
        ("Srednia latencja (ms)", metrics.avg_latency_ms),
        ("P50 latencja (ms)", metrics.p50_latency_ms),
        ("P95 latencja (ms)", metrics.p95_latency_ms),
        ("Czas trwania (s)", metrics.elapsed_seconds),
    ]
    for row in rows:
        ws.append(row)

    for col in ws.columns:
        max_length = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max_length + 2

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=metryki_run_{run_id}.xlsx"},
    )


@router.get("/compare/{run_a}/{run_b}")
def compare_runs(
    run_a: int,
    run_b: int,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    """Compare two analysis runs - show price/profitability changes per EAN."""
    from app.models.analysis_run_item import AnalysisRunItem as ARI

    ra = analysis_service.get_run_status(db, run_a)
    rb = analysis_service.get_run_status(db, run_b)
    if not ra or not rb:
        raise HTTPException(status_code=404, detail="Nie znaleziono jednego lub obu uruchomien")
    _verify_run_access(ra, current_user)
    _verify_run_access(rb, current_user)

    items_a = {i.ean: i for i in db.query(ARI).filter(ARI.analysis_run_id == run_a).all()}
    items_b = {i.ean: i for i in db.query(ARI).filter(ARI.analysis_run_id == run_b).all()}

    all_eans = sorted(set(items_a.keys()) | set(items_b.keys()))
    diffs = []
    for ean in all_eans:
        a = items_a.get(ean)
        b = items_b.get(ean)
        price_a = float(a.allegro_price) if a and a.allegro_price else None
        price_b = float(b.allegro_price) if b and b.allegro_price else None
        price_change = None
        if price_a is not None and price_b is not None:
            price_change = round(price_b - price_a, 2)
        diffs.append({
            "ean": ean,
            "run_a_price": price_a,
            "run_b_price": price_b,
            "price_change": price_change,
            "run_a_status": a.scrape_status.value if a and a.scrape_status else None,
            "run_b_status": b.scrape_status.value if b and b.scrape_status else None,
            "run_a_profitable": a.profitability_label.value if a and a.profitability_label else None,
            "run_b_profitable": b.profitability_label.value if b and b.profitability_label else None,
        })

    changed = [d for d in diffs if d["price_change"] and d["price_change"] != 0]
    return {
        "run_a": run_a,
        "run_b": run_b,
        "total_eans": len(all_eans),
        "changed": len(changed),
        "only_in_a": len(set(items_a.keys()) - set(items_b.keys())),
        "only_in_b": len(set(items_b.keys()) - set(items_a.keys())),
        "diffs": diffs[:500],
    }


@router.get("/{run_id}", response_model=AnalysisStatusResponse)
def get_analysis_status(run_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    _verify_run_access(run, current_user)
    return run


@router.get("/{run_id}/download")
def download_results(run_id: int, inline: bool = False, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    _verify_run_access(run, current_user)
    if run.status not in {AnalysisStatus.completed, AnalysisStatus.stopped}:
        raise HTTPException(status_code=400, detail="Analiza jeszcze nie zakonczona")

    content = export_run_bytes(db, run_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    filename = f"analysis_{run_id}.xlsx"
    disposition = "inline" if inline else f'attachment; filename="{filename}"'
    headers = {"Content-Disposition": disposition}
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/{run_id}/results", response_model=AnalysisResultsResponse)
def get_analysis_results(
    run_id: int,
    offset: int = 0,
    limit: int = 100,
    debug: bool = False,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    run = analysis_service.get_run_status(db, run_id)
    _verify_run_access(run, current_user)
    offset = max(0, offset)
    limit = max(1, min(limit, 1000))
    results = analysis_service.get_run_results(
        db,
        run_id=run_id,
        offset=offset,
        limit=limit,
        include_debug=debug,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    if not debug:
        payload = jsonable_encoder(
            results,
            exclude={"items": {"__all__": {"profitability_debug"}}},
        )
        return JSONResponse(content=payload)
    return results


@router.get("/{run_id}/results/updates", response_model=AnalysisResultsResponse)
def get_analysis_results_updates(
    run_id: int,
    since: Optional[datetime] = None,
    since_id: Optional[int] = None,
    limit: int = 200,
    debug: bool = False,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    run = analysis_service.get_run_status(db, run_id)
    _verify_run_access(run, current_user)
    results = analysis_service.get_run_results_since(
        db,
        run_id=run_id,
        since=since,
        since_id=since_id,
        limit=limit,
        include_debug=debug,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    if not debug:
        payload = jsonable_encoder(
            results,
            exclude={"items": {"__all__": {"profitability_debug"}}},
        )
        return JSONResponse(content=payload)
    return results


@router.get("/{run_id}/stream")
async def stream_analysis(
    run_id: int,
    request: Request,
    debug: bool = False,
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    # Verify access before starting stream
    with SessionLocal() as db:
        run = analysis_service.get_run_status(db, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
        _verify_run_access(run, current_user)

    async def event_generator():
        last_status = None
        last_processed = None
        last_total = None
        last_error = None
        last_heartbeat = datetime.now(timezone.utc)
        since = None
        since_id = None

        while True:
            if await request.is_disconnected():
                break

            now = datetime.now(timezone.utc)
            with SessionLocal() as db:
                run = analysis_service.get_run_status(db, run_id)
                if not run:
                    yield _sse_event("error", {"message": "Analysis not found"})
                    break

                status_payload = {
                    "id": run.id,
                    "status": run.status,
                    "processed_products": run.processed_products,
                    "total_products": run.total_products,
                    "error_message": run.error_message,
                    "updated_at": now.isoformat(),
                }

                status_changed = last_status != run.status or last_error != run.error_message
                progress_changed = (
                    last_processed != run.processed_products
                    or last_total != run.total_products
                )

                if status_changed:
                    yield _sse_event("status", status_payload)
                if progress_changed:
                    yield _sse_event("progress", status_payload)

                if status_changed:
                    last_status = run.status
                    last_error = run.error_message
                if progress_changed:
                    last_processed = run.processed_products
                    last_total = run.total_products

                updates = analysis_service.get_run_results_since(
                    db,
                    run_id=run_id,
                    since=since,
                    since_id=since_id,
                    limit=200,
                    include_debug=debug,
                )
                if updates:
                    if updates.items:
                        since = updates.next_since or since
                        since_id = updates.next_since_id or since_id
                        for item in updates.items:
                            row_payload = (
                                item.dict(exclude={"profitability_debug"})
                                if not debug
                                else item.dict()
                            )
                            yield _sse_event("row", row_payload)
                            if (
                                item.scrape_status in {ScrapeStatus.error, ScrapeStatus.network_error, ScrapeStatus.blocked}
                                or item.scrape_error_message
                            ):
                                yield _sse_event(
                                    "error",
                                    {
                                        "message": item.scrape_error_message or "Błąd scrapingu",
                                        "item_id": item.id,
                                        "ean": item.ean,
                                    },
                                )
                    elif since is None:
                        since = now
                        since_id = 0

                if run.status in {AnalysisStatus.completed, AnalysisStatus.failed, AnalysisStatus.canceled, AnalysisStatus.stopped}:
                    if run.status == AnalysisStatus.stopped:
                        stop_meta = run.run_metadata or {}
                        yield _sse_event("stopped", {
                            "reason": stop_meta.get("stop_reason", "unknown"),
                            "details": stop_meta.get("stop_details", {}),
                            "stopped_at_item": stop_meta.get("stopped_at_item"),
                        })
                    yield _sse_event(
                        "done",
                        {
                            "id": run.id,
                            "status": run.status,
                            "processed_products": run.processed_products,
                            "total_products": run.total_products,
                            "error_message": run.error_message,
                            "updated_at": now.isoformat(),
                        },
                    )
                    break

            if (now - last_heartbeat).total_seconds() >= STREAM_HEARTBEAT_INTERVAL:
                yield _sse_event("heartbeat", {"ts": now.isoformat()})
                last_heartbeat = now

            await asyncio.sleep(STREAM_POLL_INTERVAL)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@router.post("/{run_id}/cancel", response_model=AnalysisStatusResponse)
def cancel_analysis_run(run_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")
    _verify_run_access(run, current_user)
    if run.status in {AnalysisStatus.completed, AnalysisStatus.failed, AnalysisStatus.stopped}:
        raise HTTPException(status_code=400, detail="Analiza jest juz zakonczona")

    run = analysis_service.cancel_analysis_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Nie znaleziono analizy")

    task_ids = set(analysis_service.list_run_task_ids(db, run_id))
    if run.root_task_id:
        task_ids.add(run.root_task_id)
    for task_id in task_ids:
        celery_app.control.revoke(task_id, terminate=True)

    log_event("run_cancel",
              user_id=str(current_user.user_id) if current_user else None,
              tenant_id=str(current_user.tenant_id) if current_user else None,
              details={"run_id": run_id})

    return run


@router.post("/{run_id}/stop", response_model=AnalysisStatusResponse)
def stop_analysis_run(run_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    """Stop a running analysis - spec-compliant alias for cancel."""
    return cancel_analysis_run(run_id, db=db, current_user=current_user)


def _start_cached_analysis(payload: AnalysisStartFromDbRequest, db: Session, current_user: Optional[CurrentUser] = None) -> AnalysisUploadResponse:
    category = db.query(Category).filter(Category.id == payload.category_id).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Kategoria nie znaleziona lub nieaktywna")

    _check_concurrent_limit(db, current_user)

    try:
        products = analysis_service.build_cached_worklist(
            db,
            category_id=category.id,
            cache_days=payload.cache_days,
            include_all_cached=payload.include_all_cached,
            only_with_data=payload.only_with_data,
            limit=payload.limit,
            source=payload.source,
            ean_contains=payload.ean_contains,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Nieprawidłowy filtr źródła danych.")

    if not products:
        raise HTTPException(status_code=400, detail="Brak produktów w bazie spełniających filtry.")

    run_metadata = {
        "cache_days": payload.cache_days,
        "include_all_cached": payload.include_all_cached,
        "only_with_data": payload.only_with_data,
        "limit": payload.limit,
        "source": payload.source,
        "ean_contains": payload.ean_contains,
    }

    run = analysis_service.prepare_cached_analysis_run(
        db,
        category,
        products,
        run_metadata=run_metadata,
    )
    if current_user:
        run.tenant_id = current_user.tenant_id
        run.user_id = current_user.user_id
    try:
        result = run_analysis_task.delay(run.id)
        run.root_task_id = result.id
        analysis_service.record_run_task(db, run, result.id, "run_analysis")
        db.commit()
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).error("Failed to enqueue task for run %s: %s", run.id, exc)
        run.status = AnalysisStatus.failed
        run.error_message = "Nie udalo sie uruchomic zadania"
        db.commit()
        raise HTTPException(status_code=503, detail="Nie udalo sie uruchomic analizy")

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)
