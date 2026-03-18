from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import auth_service

router = APIRouter(tags=["tenants"])


import os
import re

REGISTRATION_KEY = os.getenv("REGISTRATION_KEY", "")


def _validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(status_code=400, detail="Haslo musi miec min 10 znakow")
    if not re.search(r"[A-Z]", password):
        raise HTTPException(status_code=400, detail="Haslo musi zawierac wielka litere")
    if not re.search(r"[0-9]", password):
        raise HTTPException(status_code=400, detail="Haslo musi zawierac cyfre")


class TenantCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z0-9_-]+$")
    plan: str = "free"
    admin_email: str = Field(..., max_length=320)
    admin_password: str = Field(..., min_length=10)
    admin_name: Optional[str] = None
    registration_key: Optional[str] = None


class TenantCreateResponse(BaseModel):
    tenant_id: str
    user_id: str
    email: str
    token: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: str
    tenant_id: str
    email: str
    role: str


@router.post("/register", response_model=TenantCreateResponse)
def register_tenant(body: TenantCreateRequest, db: Session = Depends(get_db)):
    """Register a new tenant with an admin user."""
    # require registration key if configured
    if REGISTRATION_KEY and body.registration_key != REGISTRATION_KEY:
        raise HTTPException(status_code=403, detail="Invalid registration key")
    _validate_password_strength(body.admin_password)

    from app.models.tenant import Tenant
    existing = db.query(Tenant).filter(Tenant.slug == body.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Tenant slug already taken")

    try:
        tenant = auth_service.create_tenant(db, name=body.name, slug=body.slug, plan=body.plan)
        user = auth_service.create_user(
            db,
            tenant_id=tenant.id,
            email=body.admin_email,
            password=body.admin_password,
            display_name=body.admin_name,
            role="owner",
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    token = auth_service.issue_token(user)
    return TenantCreateResponse(
        tenant_id=str(tenant.id),
        user_id=str(user.id),
        email=user.email,
        token=token,
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = auth_service.issue_token(user)
    return LoginResponse(
        token=token,
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        email=user.email,
        role=user.role,
    )
