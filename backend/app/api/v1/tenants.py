from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import auth_service
from app.services.audit_service import log_event

router = APIRouter(tags=["tenants"])

# Lazy import to avoid circular dependency - limiter lives on the app instance
def _get_limiter():
    from app.main import limiter
    return limiter


import os
import re

REGISTRATION_KEY = os.getenv("REGISTRATION_KEY", "")


def _validate_password_strength(password: str) -> None:
    errors = []
    if len(password) < 12:
        errors.append("Haslo musi miec minimum 12 znakow")
    if not re.search(r"[A-Z]", password):
        errors.append("Haslo musi zawierac wielka litere")
    if not re.search(r"[a-z]", password):
        errors.append("Haslo musi zawierac mala litere")
    if not re.search(r"[0-9]", password):
        errors.append("Haslo musi zawierac cyfre")
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append("Haslo musi zawierac znak specjalny")

    # Common password check
    common = {"password", "12345678", "qwerty123", "admin123", "letmein", "welcome1"}
    if password.lower() in common:
        errors.append("Haslo jest zbyt popularne")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))


class TenantCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z0-9_-]+$")
    plan: str = "free"
    admin_email: str = Field(..., max_length=320)
    admin_password: str = Field(..., min_length=12)
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
    log_event("tenant_register", user_id=str(user.id), tenant_id=str(tenant.id),
              details={"email": user.email, "slug": body.slug})
    return TenantCreateResponse(
        tenant_id=str(tenant.id),
        user_id=str(user.id),
        email=user.email,
        token=token,
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    auth_service.check_account_lock(body.email)
    user = auth_service.authenticate(db, body.email, body.password)
    if not user:
        auth_service.record_failed_login(body.email)
        log_event("api_login_failure", details={"email": body.email})
        raise HTTPException(status_code=401, detail="Invalid credentials")
    auth_service.record_successful_login(body.email)
    token = auth_service.issue_token(user)
    log_event("api_login_success", user_id=str(user.id), tenant_id=str(user.tenant_id),
              details={"email": user.email})
    return LoginResponse(
        token=token,
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        email=user.email,
        role=user.role,
    )
