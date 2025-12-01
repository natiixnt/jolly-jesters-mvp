import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.analysis_run import AnalysisRun
from app.models.enums import AnalysisStatus
from app.models.category import Category
from app.schemas.analysis import AnalysisStatusResponse, AnalysisUploadResponse
from app.services import analysis_service
from app.services.export_service import export_run_to_stream
from app.services.import_service import store_uploaded_file
from app.workers.tasks import run_analysis_task

router = APIRouter(tags=["analysis"])


@router.post("/upload", response_model=AnalysisUploadResponse)
async def upload_analysis(
    file: UploadFile = File(...),
    category_id: str = Form(...),
    mode: str = Form("mixed"),
    db: Session = Depends(get_db),
):
    mode = (mode or "mixed").lower()
    try:
        category_uuid = uuid.UUID(category_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid category id")

    category = db.query(Category).filter(Category.id == category_uuid).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    if mode not in {"mixed", "offline"}:
        raise HTTPException(status_code=400, detail="Mode must be 'mixed' or 'offline'")

    filepath = store_uploaded_file(file, settings.upload_dir)
    run = analysis_service.start_run(db, category, filepath.name)

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

    stream = export_run_to_stream(db, run_id)
    filename = f"analysis_{run_id}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
