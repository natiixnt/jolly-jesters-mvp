import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.schemas.settings import (
    NetworkProxyHealthSummary,
    NetworkProxyImportResult,
    NetworkProxyOut,
    NetworkProxyQuarantineRequest,
)
from app.services import proxy_pool_service
from app.services.audit_service import log_event

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
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    proxies = proxy_pool_service.list_proxies(db, active_only=active_only, include_quarantined=include_quarantined)
    for p in proxies:
        p.url = _mask_url(p.url)
    return proxies


@router.get("/health", response_model=NetworkProxyHealthSummary)
def proxy_health(db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    return proxy_pool_service.get_health_summary(db)


@router.post("/import", response_model=NetworkProxyImportResult)
@limiter.limit("5/minute")
async def import_proxies(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    if not file.filename or not file.filename.lower().endswith((".txt", ".csv", ".list")):
        raise HTTPException(status_code=400, detail="Plik musi byc .txt/.csv/.list")

    # Enforce max upload size (5 MB) - read in chunks
    MAX_UPLOAD_BYTES = 5 * 1024 * 1024
    chunks = []
    total_size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"Plik za duzy (max {MAX_UPLOAD_BYTES // (1024*1024)} MB)")
        chunks.append(chunk)
    data = b"".join(chunks)

    # Validate content is valid UTF-8 text (not a binary file)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Plik musi byc tekstowy (UTF-8)")

    try:
        result = proxy_pool_service.import_from_text(db, data)
    except ValueError as exc:
        logger.warning("Proxy pool import validation error: %s", exc)
        raise HTTPException(status_code=400, detail="Nieprawidlowy format listy proxy")
    log_event("proxy_import",
              user_id=str(current_user.user_id) if current_user else None,
              tenant_id=str(current_user.tenant_id) if current_user else None,
              ip=request.client.host if request.client else None,
              details={"filename": file.filename, "added": result.added, "skipped": result.skipped})
    return result


@router.post("/{proxy_id}/quarantine", response_model=NetworkProxyOut)
def quarantine(proxy_id: int, body: NetworkProxyQuarantineRequest, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    proxy = proxy_pool_service.quarantine_proxy(
        db, proxy_id,
        duration_minutes=body.duration_minutes,
        reason=body.reason,
    )
    if not proxy:
        raise HTTPException(status_code=404, detail="Nie znaleziono proxy")
    return proxy


@router.delete("/{proxy_id}/quarantine", response_model=NetworkProxyOut)
def unquarantine(proxy_id: int, db: Session = Depends(get_db), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    proxy = proxy_pool_service.unquarantine_proxy(db, proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="Nie znaleziono proxy")
    return proxy
