from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.services.analysis_service import get_run_items
from app.utils.excel_writer import build_analysis_workbook


def export_run_to_stream(db: Session, run_id: int):
    items = get_run_items(db, run_id)
    return build_analysis_workbook(items)


def export_run_to_disk(db: Session, run_id: int, export_dir: Path) -> Optional[Path]:
    if not export_dir.exists():
        export_dir.mkdir(parents=True, exist_ok=True)

    stream = export_run_to_stream(db, run_id)
    filepath = export_dir / f"analysis_{run_id}.xlsx"
    with open(filepath, "wb") as f:
        f.write(stream.getvalue())
    return filepath
