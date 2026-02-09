from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.utils.allegro_scraper_client import check_scraper_health

router = APIRouter()


@router.get("/status", summary="Lightweight health/status for UI diagnostics")
def status(db: Session = Depends(get_db)) -> dict:
    # DB liveness
    db.execute(text("SELECT 1"))

    scraper = check_scraper_health()
    overall = "ok" if scraper.get("status") == "ok" else "degraded"

    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scraper": scraper,
    }
