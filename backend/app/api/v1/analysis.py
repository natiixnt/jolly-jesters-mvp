import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.category import Category
from app.models.enums import AnalysisStatus
from app.schemas.analysis import AnalysisStatusResponse, AnalysisUploadResponse, AnalysisRunSummary
from app.services import analysis_service
from app.services.export_service import export_run_bytes
from app.services.import_service import handle_upload
from app.services.schemas import ScrapingStrategyConfig
from app.workers.tasks import run_analysis_task

router = APIRouter(tags=["analysis"])


def _validate_strategy(use_api: bool, use_cloud_http: bool, use_local_scraper: bool):
    if not any([use_api, use_cloud_http, use_local_scraper]):
        raise HTTPException(
            status_code=400,
            detail="At least one scraper strategy (API, cloud HTTP or local) must be enabled for an analysis run.",
        )


@router.post("/upload", response_model=AnalysisUploadResponse)
async def upload_analysis(
    file: UploadFile = File(...),
    category_id: str = Form(...),
    mode: str = Form("mixed"),
    use_api: bool = Form(True),
    use_cloud_http: bool = Form(True),
    use_local_scraper: bool = Form(True),
    db: Session = Depends(get_db),
):
    mode = (mode or "mixed").lower()
    _validate_strategy(use_api, use_cloud_http, use_local_scraper)

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
        use_api=use_api,
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
def download_results(run_id: int, db: Session = Depends(get_db)):
    run = analysis_service.get_run_status(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if run.status != AnalysisStatus.completed:
        raise HTTPException(status_code=400, detail="Analysis not completed yet")

    content = export_run_bytes(db, run_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    filename = f"analysis_{run_id}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename=\"{filename}\"'}
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("", response_model=list[AnalysisRunSummary])
def list_runs(db: Session = Depends(get_db), limit: int = 20):
    return analysis_service.list_recent_runs(db, limit=limit)
