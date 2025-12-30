import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.category import Category
from app.models.enums import AnalysisStatus
from app.schemas.analysis import (
    AnalysisResultsResponse,
    AnalysisRetryResponse,
    AnalysisRunListResponse,
    AnalysisStartFromDbRequest,
    AnalysisStatusResponse,
    AnalysisUploadResponse,
    AnalysisRunSummary,
)
from app.services import analysis_service
from app.core.config import settings
from app.services.export_service import export_run_bytes
from app.services.import_service import handle_upload
from app.services.schemas import ScrapingStrategyConfig
from app.utils.local_scraper_client import check_local_scraper_health
from app.workers.tasks import celery_app, run_analysis_task, scrape_one_cloud, scrape_one_local

router = APIRouter(tags=["analysis"])


def _validate_strategy(use_cloud_http: bool, use_local_scraper: bool):
    if not any([use_cloud_http, use_local_scraper]):
        raise HTTPException(
            status_code=400,
            detail="At least one scraper strategy (cloud HTTP or local) must be enabled for an analysis run.",
        )


def _validate_scraper_config(use_cloud_http: bool, use_local_scraper: bool):
    # cloud HTTP
    if use_cloud_http:
        proxies = settings.PROXY_LIST
        if isinstance(proxies, str):
            proxies = [p for p in proxies.split(",") if p.strip()]
        if not proxies:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Brak skonfigurowanych proxy dla cloud HTTP (PROXY_LIST). "
                    "Odznacz 'Proxy / Cloud scraper' lub ustaw liste proxy."
                ),
            )

    # local Selenium
    if use_local_scraper and not settings.LOCAL_SCRAPER_URL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Brak adresu uslugi lokalnego scrapera (LOCAL_SCRAPER_URL). "
                "Odznacz 'Local scraper (Selenium)' albo skonfiguruj URL. "
                "W Dockerze: LOCAL_SCRAPER_URL=http://local_scraper:5050, "
                "w Kubernetes: np. http://local-scraper.default.svc.cluster.local:5050."
            ),
        )


@router.post("/upload", response_model=AnalysisUploadResponse)
async def upload_analysis(
    file: UploadFile = File(...),
    category_id: str = Form(...),
    mode: str = Form("mixed"),
    use_cloud_http: bool = Form(True),
    use_local_scraper: bool = Form(True),
    db: Session = Depends(get_db),
):
    mode = (mode or "mixed").lower()
    _validate_strategy(use_cloud_http, use_local_scraper)
    _validate_scraper_config(use_cloud_http, use_local_scraper)
    if use_local_scraper:
        health = check_local_scraper_health(timeout_seconds=2.0)
        if health.get("status") != "ok":
            url = health.get("url") or settings.LOCAL_SCRAPER_URL or "LOCAL_SCRAPER_URL"
            status_label = health.get("status") or "unknown"
            status_code = health.get("status_code")
            error = health.get("error")
            suffix = f", status_code={status_code}" if status_code else ""
            if error:
                suffix += f", error={error}"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Local scraper niedostepny lub niezdrowy "
                    f"(status={status_label}{suffix}). "
                    f"Sprawdz usluge pod {url} albo wylacz 'Local scraper (Selenium)' "
                    "i uzyj 'Proxy / Cloud scraper'."
                ),
            )

    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category id")

    category = db.query(Category).filter(Category.id == category_uuid).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Category not found or inactive")

    if mode not in {"mixed", "offline", "online"}:
        raise HTTPException(status_code=400, detail="Mode must be 'mixed', 'offline' or 'online'")

    if not file.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(status_code=400, detail="Plik musi być w formacie Excel (.xls/.xlsx)")

    strategy = ScrapingStrategyConfig(
        use_cloud_http=use_cloud_http,
        use_local_scraper=use_local_scraper,
    )

    try:
        run = await handle_upload(db, category, file, strategy, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = run_analysis_task.delay(run.id, mode=mode)
    run.root_task_id = result.id
    analysis_service.record_run_task(db, run, result.id, "run_analysis")
    db.commit()

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)


@router.post("/start_from_db", response_model=AnalysisUploadResponse)
def start_from_db(payload: AnalysisStartFromDbRequest, db: Session = Depends(get_db)):
    return _start_cached_analysis(payload, db)


@router.post("/run_from_db", response_model=AnalysisUploadResponse)
def run_from_db(payload: AnalysisStartFromDbRequest, db: Session = Depends(get_db)):
    return _start_cached_analysis(payload, db)


@router.post("/run_from_cache", response_model=AnalysisUploadResponse)
def run_from_cache(payload: AnalysisStartFromDbRequest, db: Session = Depends(get_db)):
    return _start_cached_analysis(payload, db)

def _start_cached_analysis(payload: AnalysisStartFromDbRequest, db: Session) -> AnalysisUploadResponse:
    mode = (payload.mode or "mixed").lower()
    _validate_strategy(payload.use_cloud_http, payload.use_local_scraper)
    _validate_scraper_config(payload.use_cloud_http, payload.use_local_scraper)
    if payload.use_local_scraper:
        health = check_local_scraper_health(timeout_seconds=2.0)
        if health.get("status") != "ok":
            url = health.get("url") or settings.LOCAL_SCRAPER_URL or "LOCAL_SCRAPER_URL"
            status_label = health.get("status") or "unknown"
            status_code = health.get("status_code")
            error = health.get("error")
            suffix = f", status_code={status_code}" if status_code else ""
            if error:
                suffix += f", error={error}"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Local scraper niedostepny lub niezdrowy "
                    f"(status={status_label}{suffix}). "
                    f"Sprawdz usluge pod {url} albo wylacz 'Local scraper (Selenium)' "
                    "i uzyj 'Proxy / Cloud scraper'."
                ),
            )

    if mode not in {"mixed", "offline", "online"}:
        raise HTTPException(status_code=400, detail="Mode must be 'mixed', 'offline' or 'online'")

    category = db.query(Category).filter(Category.id == payload.category_id).first()
    if not category or not category.is_active:
        raise HTTPException(status_code=404, detail="Category not found or inactive")

    strategy = ScrapingStrategyConfig(
        use_cloud_http=payload.use_cloud_http,
        use_local_scraper=payload.use_local_scraper,
    )

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
        strategy,
        mode=mode,
        run_metadata=run_metadata,
    )
    result = run_analysis_task.delay(run.id, mode=mode)
    run.root_task_id = result.id
    analysis_service.record_run_task(db, run, result.id, "run_analysis")
    db.commit()

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)


@router.get("", response_model=list[AnalysisRunSummary])
def list_runs(db: Session = Depends(get_db), limit: int = 20):
    return analysis_service.list_recent_runs(db, limit=limit)


@router.get("/active", response_model=AnalysisRunListResponse)
def list_active_runs(db: Session = Depends(get_db), limit: int = 20):
    runs = analysis_service.list_active_runs(db, limit=limit)
    return {"runs": runs}


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
    disposition = "inline" if inline else f'attachment; filename=\"{filename}\"'
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


@router.post("/{run_id}/retry_failed", response_model=AnalysisRetryResponse)
def retry_failed_items(
    run_id: int,
    strategy: str = "cloud",
    db: Session = Depends(get_db),
):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if run.status == AnalysisStatus.canceled:
        raise HTTPException(status_code=400, detail="Analysis was canceled")

    strategy = (strategy or "cloud").lower()
    if strategy not in {"cloud", "local"}:
        raise HTTPException(status_code=400, detail="Strategy must be 'cloud' or 'local'")

    use_cloud = strategy == "cloud"
    use_local = strategy == "local"
    _validate_scraper_config(use_cloud, use_local)

    def _enqueue(item):
        if use_cloud:
            result = scrape_one_cloud.delay(item.ean, item.id, {"use_cloud_http": True, "use_local_scraper": False})
            return result.id
        result = scrape_one_local.delay(item.ean, item.id, {"use_cloud_http": False, "use_local_scraper": True})
        return result.id

    scheduled = analysis_service.retry_failed_items(db, run_id, enqueue=_enqueue)
    return AnalysisRetryResponse(run_id=run.id, status=run.status, scheduled=scheduled)
