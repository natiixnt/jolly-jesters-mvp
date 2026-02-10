from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.models.category import Category
from app.models.enums import AnalysisStatus, ScrapeStatus
from app.schemas.analysis import (
    AnalysisResultsResponse,
    AnalysisRunListResponse,
    AnalysisRunSummary,
    AnalysisStartFromDbRequest,
    AnalysisStatusResponse,
    AnalysisUploadResponse,
)
from app.services import analysis_service
from app.services.export_service import export_run_bytes
from app.services.import_service import handle_upload
from app.workers.tasks import celery_app, run_analysis_task

router = APIRouter(tags=["analysis"])

STREAM_POLL_INTERVAL = 2.0
STREAM_HEARTBEAT_INTERVAL = 5.0


def _sse_event(event_type: str, payload: dict) -> str:
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


@router.post("/upload", response_model=AnalysisUploadResponse)
async def upload_analysis(
    file: UploadFile = File(...),
    category_id: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category id")

    category = db.query(Category).filter(Category.id == category_uuid).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Category not found or inactive")

    if not file.filename.lower().endswith((".xls", ".xlsx", ".csv")):
        raise HTTPException(status_code=400, detail="Plik musi być .xls/.xlsx/.csv")

    run = await handle_upload(db, category, file)
    result = run_analysis_task.delay(run.id)
    run.root_task_id = result.id
    analysis_service.record_run_task(db, run, result.id, "run_analysis")
    db.commit()

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)


@router.post("/run_from_db", response_model=AnalysisUploadResponse)
def run_from_db(payload: AnalysisStartFromDbRequest, db: Session = Depends(get_db)):
    return _start_cached_analysis(payload, db)


@router.get("", response_model=list[AnalysisRunSummary])
def list_runs(db: Session = Depends(get_db), limit: int = 20):
    return analysis_service.list_recent_runs(db, limit=limit)


@router.get("/active", response_model=AnalysisRunListResponse)
def list_active_runs(db: Session = Depends(get_db), limit: int = 20):
    runs = analysis_service.list_active_runs(db, limit=limit)
    return {"runs": runs}


@router.get("/latest", response_model=AnalysisStatusResponse)
def latest_run(db: Session = Depends(get_db)):
    run = analysis_service.get_latest_run(db)
    if not run:
        raise HTTPException(status_code=404, detail="Brak uruchomień")
    return run


@router.get("/{run_id}", response_model=AnalysisStatusResponse)
def get_analysis_status(run_id: int, db: Session = Depends(get_db)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return run


@router.get("/{run_id}/download")
def download_results(run_id: int, inline: bool = False, db: Session = Depends(get_db)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if run.status != AnalysisStatus.completed:
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
    db: Session = Depends(get_db),
):
    results = analysis_service.get_run_results(db, run_id=run_id, offset=offset, limit=limit)
    if not results:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return results


@router.get("/{run_id}/results/updates", response_model=AnalysisResultsResponse)
def get_analysis_results_updates(
    run_id: int,
    since: Optional[datetime] = None,
    since_id: Optional[int] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    results = analysis_service.get_run_results_since(
        db,
        run_id=run_id,
        since=since,
        since_id=since_id,
        limit=limit,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return results


@router.get("/{run_id}/stream")
async def stream_analysis(run_id: int, request: Request):
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
                )
                if updates:
                    if updates.items:
                        since = updates.next_since or since
                        since_id = updates.next_since_id or since_id
                        for item in updates.items:
                            yield _sse_event("row", item.dict())
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

                if run.status in {AnalysisStatus.completed, AnalysisStatus.failed, AnalysisStatus.canceled}:
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
def cancel_analysis_run(run_id: int, db: Session = Depends(get_db)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if run.status in {AnalysisStatus.completed, AnalysisStatus.failed}:
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


def _start_cached_analysis(payload: AnalysisStartFromDbRequest, db: Session) -> AnalysisUploadResponse:
    category = db.query(Category).filter(Category.id == payload.category_id).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Category not found or inactive")

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
    result = run_analysis_task.delay(run.id)
    run.root_task_id = result.id
    analysis_service.record_run_task(db, run, result.id, "run_analysis")
    db.commit()

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)
