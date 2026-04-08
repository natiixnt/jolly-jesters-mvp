from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import CurrentUser, get_current_user_optional
from app.schemas.settings import ProxyMeta, ProxyReloadResponse
from app.services import proxy_service

router = APIRouter(tags=["proxies"])


@router.get("", response_model=ProxyMeta)
def get_proxy_meta(current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    meta = proxy_service.get_metadata()
    return ProxyMeta(**meta)


@router.post("", response_model=ProxyMeta)
async def upload_proxy_list(file: UploadFile = File(...), current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
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

    try:
        meta = proxy_service.save_list(data, reload=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    meta["uploaded_at"] = datetime.utcnow()
    return ProxyMeta(**meta)


@router.post("/reload", response_model=ProxyReloadResponse)
def reload_proxies(current_user: Optional[CurrentUser] = Depends(get_current_user_optional)):
    result = proxy_service.reload_proxies()
    status = result.get("status") or "error"
    if status != "ok":
        raise HTTPException(status_code=503, detail=str(result))
    return ProxyReloadResponse(
        status="ok",
        count=result.get("count"),
        path=result.get("path"),
    )
