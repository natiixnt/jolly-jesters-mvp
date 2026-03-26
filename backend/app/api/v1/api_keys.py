from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import api_key_service

router = APIRouter(tags=["api-keys"])

TENANT = "00000000-0000-0000-0000-000000000000"


class CreateKeyRequest(BaseModel):
    name: str


class APIKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: Optional[str]
    expires_at: Optional[str]
    created_at: str


class APIKeyCreated(BaseModel):
    id: int
    name: str
    key: str  # full key - shown only once
    key_prefix: str
    created_at: str


@router.get("/", response_model=List[APIKeyOut])
def list_keys(db: Session = Depends(get_db)):
    keys = api_key_service.list_keys(db, tenant_id=TENANT)
    return [
        APIKeyOut(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            is_active=k.is_active,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            expires_at=k.expires_at.isoformat() if k.expires_at else None,
            created_at=k.created_at.isoformat() if k.created_at else "",
        )
        for k in keys
    ]


@router.post("/", response_model=APIKeyCreated)
def create_key(req: CreateKeyRequest, db: Session = Depends(get_db)):
    record, raw_key = api_key_service.create_api_key(db, tenant_id=TENANT, name=req.name)
    return APIKeyCreated(
        id=record.id,
        name=record.name,
        key=raw_key,
        key_prefix=record.key_prefix,
        created_at=record.created_at.isoformat() if record.created_at else "",
    )


@router.delete("/{key_id}")
def revoke_key(key_id: int, db: Session = Depends(get_db)):
    ok = api_key_service.revoke_key(db, tenant_id=TENANT, key_id=key_id)
    if not ok:
        raise HTTPException(404, "API key not found")
    return {"status": "ok"}
