from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun
from app.services.analysis_service import get_run_items
from app.utils.excel_writer import build_analysis_excel


def export_run_bytes(db: Session, run_id: int) -> Optional[bytes]:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        return None
    items = get_run_items(db, run_id)
    category_name = run.category.name if run.category else ""
    return build_analysis_excel(items, category_name)


def export_run_to_disk(db: Session, run_id: int, export_dir: Path) -> Optional[Path]:
    if not export_dir.exists():
        export_dir.mkdir(parents=True, exist_ok=True)

    content = export_run_bytes(db, run_id)
    if content is None:
        return None
    filepath = export_dir / f"analysis_{run_id}.xlsx"
    filepath.write_bytes(content)
    return filepath
