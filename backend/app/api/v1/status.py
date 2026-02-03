import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.utils.brightdata_browser import brightdata_status
from app.utils.local_scraper_client import check_local_scraper_health

router = APIRouter()


@router.get("/status", summary="Lightweight health/status for UI diagnostics")
def status(db: Session = Depends(get_db)) -> dict:
    # DB liveness
    db.execute(text("SELECT 1"))

    mode = (os.getenv("SCRAPER_MODE") or "decodo").strip().lower()
    brightdata = brightdata_status()
    local = check_local_scraper_health()

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scraper_mode": mode,
        "scraper_provider": mode,
        "brightdata": {
            "mode": brightdata.get("mode"),
            "metrics": brightdata.get("metrics"),
        },
        "local_scraper": {"status": local.get("status")},
    }
