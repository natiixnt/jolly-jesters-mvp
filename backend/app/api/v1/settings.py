from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.settings import SettingsRead, SettingsUpdate
from app.services import settings_service

router = APIRouter(tags=["settings"])


@router.get("/", response_model=SettingsRead)
def get_settings(db: Session = Depends(get_db)):
    record = settings_service.get_settings(db)
    if not record:
        raise HTTPException(status_code=404, detail="Settings not found")
    return record


@router.put("/", response_model=SettingsRead)
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    record = settings_service.update_settings(
        db=db,
        cache_ttl_days=payload.cache_ttl_days,
        local_scraper_windows=payload.local_scraper_windows,
    )
    return record
