import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.schemas.settings import ProxyMeta, ProxyReloadResponse
from app.services import proxy_service, proxy_pool_service

router = APIRouter(tags=["proxies"])

# Regex for host:port:user:pass format (common proxy list format)
_HOST_PORT_USER_PASS = re.compile(r'^([^:\s]+):(\d+):([^:\s]+):([^:\s]+)$')


def _normalize_proxy_line(line: str) -> str:
    """Convert host:port:user:pass to http://user:pass@host:port if needed.
    Lines already in URL format (http://, socks5://, etc.) are returned as-is.
    Plain host:port (no auth) gets http:// prefix."""
    line = line.strip()
    if not line:
        return line
    # Already a URL
    if re.match(r'^(https?|socks[45])://', line):
        return line
    # host:port:user:pass
    m = _HOST_PORT_USER_PASS.match(line)
    if m:
        host, port, user, passwd = m.groups()
        return f'http://{user}:{passwd}@{host}:{port}'
    # host:port (no auth)
    if re.match(r'^[^:\s]+:\d+$', line):
        return f'http://{line}'
    return line


def _normalize_proxy_data(data: bytes) -> bytes:
    """Normalize all proxy lines in uploaded data to URL format."""
    text = data.decode("utf-8", errors="ignore")
    lines = [_normalize_proxy_line(ln) for ln in text.splitlines() if ln.strip()]
    return ("\n".join(lines) + "\n").encode("utf-8")


@router.get("", response_model=ProxyMeta)
def get_proxy_meta(current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    meta = proxy_service.get_metadata()
    return ProxyMeta(**meta)


@router.post("", response_model=ProxyMeta)
async def upload_proxy_list(
    file: UploadFile = File(...),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith((".txt", ".list", ".cfg")):
        raise HTTPException(status_code=400, detail="Plik musi byc tekstowy (.txt/.list/.cfg)")

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

    # Normalize proxy format (host:port:user:pass -> URL)
    data = _normalize_proxy_data(data)

    try:
        meta = proxy_service.save_list(data, reload=True)
    except ValueError as exc:
        logger.warning("Proxy list validation error: %s", exc)
        raise HTTPException(status_code=400, detail="Nieprawidlowy format listy proxy")

    # Also persist to DB for health tracking and UI display
    try:
        db_result = proxy_pool_service.import_from_text(db, data)
        meta["db_imported"] = db_result.get("imported", 0)
        meta["db_skipped"] = db_result.get("skipped", 0)
    except Exception as exc:
        logger.warning("Proxy DB import failed (file saved OK): %s", exc)

    meta["uploaded_at"] = datetime.utcnow()
    return ProxyMeta(**meta)


@router.post("/reload", response_model=ProxyReloadResponse)
def reload_proxies(current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    result = proxy_service.reload_proxies()
    status = result.get("status") or "error"
    if status != "ok":
        logger.warning("Proxy reload failed: %s", result)
        raise HTTPException(status_code=503, detail="Blad przeladowania listy proxy")
    return ProxyReloadResponse(
        status="ok",
        count=result.get("count"),
        path=result.get("path"),
    )
