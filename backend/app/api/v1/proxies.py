from datetime import datetime

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.settings import ProxyMeta, ProxyReloadResponse
from app.services import proxy_service

router = APIRouter(tags=["proxies"])


@router.get("", response_model=ProxyMeta)
def get_proxy_meta():
    meta = proxy_service.get_metadata()
    return ProxyMeta(**meta)


@router.post("", response_model=ProxyMeta)
async def upload_proxy_list(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".txt", ".list", ".cfg")):
        raise HTTPException(status_code=400, detail="Plik musi być tekstowy (.txt)")
    data = await file.read()
    try:
        meta = proxy_service.save_list(data, reload=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    meta["uploaded_at"] = datetime.utcnow()
    return ProxyMeta(**meta)


@router.post("/reload", response_model=ProxyReloadResponse)
def reload_proxies():
    result = proxy_service.reload_proxies()
    status = result.get("status") or "error"
    if status != "ok":
        raise HTTPException(status_code=503, detail=str(result))
    return ProxyReloadResponse(
        status="ok",
        count=result.get("count"),
        path=result.get("path"),
    )
