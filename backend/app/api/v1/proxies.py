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
    """Convert various proxy formats to proper URL format.

    Supported input formats:
      host:port:user:pass           -> http://user:pass@host:port
      protocol://host:port:user:pass -> protocol://user:pass@host:port
      protocol://user:pass@host:port -> as-is (already correct)
      host:port                      -> http://host:port
    """
    line = line.strip()
    if not line:
        return line

    # Extract protocol prefix if present
    protocol = 'http'
    rest = line
    proto_match = re.match(r'^(https?|socks[45])://(.*)', line)
    if proto_match:
        protocol = proto_match.group(1)
        rest = proto_match.group(2)

    # If rest already has @ sign, it's user:pass@host:port - already correct
    if '@' in rest:
        return f'{protocol}://{rest}'

    # Try to parse as host:port:user:pass
    # Split on : and figure out what we have
    parts = rest.split(':')
    if len(parts) == 4:
        # host:port:user:pass
        host, port, user, passwd = parts
        return f'{protocol}://{user}:{passwd}@{host}:{port}'
    if len(parts) == 2:
        # host:port (no auth)
        return f'{protocol}://{rest}'
    if len(parts) > 4:
        # host:port:user:pass_with_colons (password may contain colons)
        host = parts[0]
        port = parts[1]
        user = parts[2]
        passwd = ':'.join(parts[3:])  # rejoin remaining as password
        return f'{protocol}://{user}:{passwd}@{host}:{port}'

    return f'{protocol}://{rest}'


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
