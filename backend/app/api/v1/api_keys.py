
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_optional
from app.db.session import get_db
from app.services import api_key_service
from app.services.audit_service import log_event

router = APIRouter(tags=["api-keys"])

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000000"


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    scopes: Optional[List[str]] = Field(None, max_length=20)  # defaults to ["read"] if not provided


class APIKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    scopes: List[str]
    is_active: bool
    last_used_at: Optional[str]
    expires_at: Optional[str]
    created_at: str


class APIKeyCreated(BaseModel):
    id: int
    name: str
    key: str  # full key - shown only once
    key_prefix: str
    scopes: List[str]
    created_at: str


@router.get("/", response_model=List[APIKeyOut])
def list_keys(
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    keys = api_key_service.list_keys(db, tenant_id=tenant_id)
    return [
        APIKeyOut(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            scopes=k.get_scopes(),
            is_active=k.is_active,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            expires_at=k.expires_at.isoformat() if k.expires_at else None,
            created_at=k.created_at.isoformat() if k.created_at else "",
        )
        for k in keys
    ]


@router.post("/", response_model=APIKeyCreated)
def create_key(
    req: CreateKeyRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    try:
        record, raw_key = api_key_service.create_api_key(
            db, tenant_id=tenant_id, name=req.name, scopes=req.scopes,
        )
    except ValueError as exc:
        logger.warning("API key creation validation error: %s", exc)
        raise HTTPException(400, "Nieprawidlowe dane klucza API")
    log_event("api_key_create", tenant_id=str(tenant_id),
              ip=request.client.host if request.client else None,
              details={"key_name": req.name, "key_prefix": record.key_prefix,
                       "scopes": record.get_scopes()})
    return APIKeyCreated(
        id=record.id,
        name=record.name,
        key=raw_key,
        key_prefix=record.key_prefix,
        scopes=record.get_scopes(),
        created_at=record.created_at.isoformat() if record.created_at else "",
    )


@router.delete("/{key_id}")
def revoke_key(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    tenant_id = current_user.tenant_id if current_user else DEFAULT_TENANT
    ok = api_key_service.revoke_key(db, tenant_id=tenant_id, key_id=key_id)
    if not ok:
        raise HTTPException(404, "Nie znaleziono klucza API")
    log_event("api_key_revoke", tenant_id=str(tenant_id),
              ip=request.client.host if request.client else None,
              details={"key_id": key_id})
    return {"status": "ok"}
