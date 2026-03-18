from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.settings import (
    NetworkProxyHealthSummary,
    NetworkProxyImportResult,
    NetworkProxyOut,
    NetworkProxyQuarantineRequest,
)
from app.services import proxy_pool_service

router = APIRouter(tags=["proxy-pool"])


def _mask_url(url: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        if p.username or p.password:
            h = f"***:***@{p.hostname}:{p.port}" if p.port else f"***:***@{p.hostname}"
            return urlunparse(p._replace(netloc=h))
    except Exception:
        pass
    return "***"


@router.get("", response_model=list[NetworkProxyOut])
def list_proxies(
    active_only: bool = False,
    include_quarantined: bool = True,
    db: Session = Depends(get_db),
):
    proxies = proxy_pool_service.list_proxies(db, active_only=active_only, include_quarantined=include_quarantined)
    for p in proxies:
        p.url = _mask_url(p.url)
    return proxies


@router.get("/health", response_model=NetworkProxyHealthSummary)
def proxy_health(db: Session = Depends(get_db)):
    return proxy_pool_service.get_health_summary(db)


@router.post("/import", response_model=NetworkProxyImportResult)
async def import_proxies(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith((".txt", ".csv", ".list")):
        raise HTTPException(status_code=400, detail="Plik musi byc .txt/.csv/.list")
    data = await file.read()
    try:
        result = proxy_pool_service.import_from_text(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.post("/{proxy_id}/quarantine", response_model=NetworkProxyOut)
def quarantine(proxy_id: int, body: NetworkProxyQuarantineRequest, db: Session = Depends(get_db)):
    proxy = proxy_pool_service.quarantine_proxy(
        db, proxy_id,
        duration_minutes=body.duration_minutes,
        reason=body.reason,
    )
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return proxy


@router.delete("/{proxy_id}/quarantine", response_model=NetworkProxyOut)
def unquarantine(proxy_id: int, db: Session = Depends(get_db)):
    proxy = proxy_pool_service.unquarantine_proxy(db, proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return proxy
