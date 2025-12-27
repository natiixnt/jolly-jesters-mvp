import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.category import Category
from app.models.enums import AnalysisStatus
from app.schemas.analysis import (
    AnalysisResultsResponse,
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
from app.workers.tasks import run_analysis_task

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

    run_analysis_task.delay(run.id, mode=mode)

    return AnalysisUploadResponse(analysis_run_id=run.id, status=run.status)


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


@router.get("", response_model=list[AnalysisRunSummary])
def list_runs(db: Session = Depends(get_db), limit: int = 20):
    return analysis_service.list_recent_runs(db, limit=limit)


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
