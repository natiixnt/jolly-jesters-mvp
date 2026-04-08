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
from app.services.export_service import export_run_bytes
from app.services.import_service import handle_upload
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
            raise HTTPException(status_code=404, detail="Analysis not found")
STREAM_HEARTBEAT_INTERVAL = 5.0


def _sse_event(event_type: str, payload: dict) -> str:
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


@router.post("/upload", response_model=AnalysisUploadResponse)
async def upload_analysis(
    file: UploadFile = File(...),
    category_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category id")

    category = db.query(Category).filter(Category.id == category_uuid).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Category not found or inactive")

    if not file.filename.lower().endswith((".xls", ".xlsx", ".csv")):
        raise HTTPException(status_code=400, detail="Plik musi byc .xls/.xlsx/.csv")

    _check_concurrent_limit(db)

    # enforce max upload size (50 MB)
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Plik za duzy (max {MAX_UPLOAD_BYTES // (1024*1024)} MB)")
    await file.seek(0)

    run = await handle_upload(db, category, file)
    if current_user:
        run.tenant_id = current_user.tenant_id
        run.user_id = current_user.user_id
    result = run_analysis_task.delay(run.id)
    run.root_task_id = result.id
    analysis_service.record_run_task(db, run, result.id, "run_analysis")
    db.commit()

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)


@router.post("/run_from_db", response_model=AnalysisUploadResponse)
def run_from_db(
    payload: AnalysisStartFromDbRequest,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    return _start_cached_analysis(payload, db, current_user=current_user)


from pydantic import BaseModel as PydanticBaseModel


class BulkEanRequest(PydanticBaseModel):
    category_id: UUID
    items: list[dict]


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
        raise HTTPException(status_code=400, detail="Items list must have 1-10000 entries")

    cat = db.query(Category).filter(Category.id == body.category_id).first()
    if not cat or not cat.is_active:
        raise HTTPException(status_code=404, detail="Category not found or inactive")

    _check_concurrent_limit(db)

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

    for idx, entry in enumerate(body.items, start=1):
        ean = str(entry.get("ean", "")).strip()
        if not ean:
            continue
        price = entry.get("purchase_price") or entry.get("price")
        name = entry.get("name", "")

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

    result = run_analysis_task.delay(run.id)
    run.root_task_id = result.id
    analysis_service.record_run_task(db, run, result.id, "run_analysis")
    db.commit()

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
    runs = analysis_service.list_active_runs(db, limit=limit, tenant_id=current_user.tenant_id if current_user else None)
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
        raise HTTPException(status_code=404, detail="Analysis not found")
    return metrics


@router.get("/{run_id}/metrics/csv")
def export_metrics_csv(run_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    run = analysis_service.get_run_status(db, run_id)
    _verify_run_access(run, current_user)
    metrics = analysis_service.get_run_metrics(db, run_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Analysis not found")
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
        raise HTTPException(status_code=404, detail="One or both runs not found")
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
        raise HTTPException(status_code=404, detail="Analysis not found")
    _verify_run_access(run, current_user)
    return run


@router.get("/{run_id}/download")
def download_results(run_id: int, inline: bool = False, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis not found")
    _verify_run_access(run, current_user)
    if run.status not in {AnalysisStatus.completed, AnalysisStatus.stopped}:
        raise HTTPException(status_code=400, detail="Analysis not completed yet")

    content = export_run_bytes(db, run_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
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
        raise HTTPException(status_code=404, detail="Analysis not found")
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
        raise HTTPException(status_code=404, detail="Analysis not found")
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
            raise HTTPException(status_code=404, detail="Analysis not found")
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
        raise HTTPException(status_code=404, detail="Analysis not found")
    _verify_run_access(run, current_user)
    if run.status in {AnalysisStatus.completed, AnalysisStatus.failed, AnalysisStatus.stopped}:
        raise HTTPException(status_code=400, detail="Analysis already finished")

    run = analysis_service.cancel_analysis_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis not found")

    task_ids = set(analysis_service.list_run_task_ids(db, run_id))
    if run.root_task_id:
        task_ids.add(run.root_task_id)
    for task_id in task_ids:
        celery_app.control.revoke(task_id, terminate=True)

    return run


def _start_cached_analysis(payload: AnalysisStartFromDbRequest, db: Session, current_user: Optional[CurrentUser] = None) -> AnalysisUploadResponse:
    category = db.query(Category).filter(Category.id == payload.category_id).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Category not found or inactive")

    _check_concurrent_limit(db)

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
    result = run_analysis_task.delay(run.id)
    run.root_task_id = result.id
    analysis_service.record_run_task(db, run, result.id, "run_analysis")
    db.commit()

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)
