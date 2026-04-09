
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.db.session import get_db
from app.services import auth_service
from app.services.audit_service import log_event

router = APIRouter(tags=["tenants"])

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
    plan: str = Field("free", max_length=50)
    admin_email: str = Field(..., max_length=320)
    admin_password: str = Field(..., min_length=12, max_length=256)
    admin_name: Optional[str] = Field(None, max_length=255)
    registration_key: Optional[str] = Field(None, max_length=255)


class TenantCreateResponse(BaseModel):
    tenant_id: str
    user_id: str
    email: str
    token: str


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    token: str
    user_id: str
    tenant_id: str
    email: str
    role: str


class RefreshRequest(BaseModel):
    token: str


class RefreshResponse(BaseModel):
    token: str


@router.post("/register", response_model=TenantCreateResponse)
@limiter.limit("3/hour")
def register_tenant(request: Request, body: TenantCreateRequest, db: Session = Depends(get_db)):
    """Register a new tenant with an admin user."""
    # require registration key if configured
    if REGISTRATION_KEY and body.registration_key != REGISTRATION_KEY:
        raise HTTPException(status_code=403, detail="Nieprawidlowy klucz rejestracji")
    _validate_password_strength(body.admin_password)

    from app.models.tenant import Tenant
    existing = db.query(Tenant).filter(Tenant.slug == body.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Ten identyfikator jest juz zajety")

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
        logger.warning("Tenant registration conflict: %s", e)
        raise HTTPException(status_code=409, detail="Nie mozna utworzyc konta. Sprawdz dane i sprobuj ponownie.")

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
@limiter.limit("5/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    auth_service.check_account_lock(body.email)
    user = auth_service.authenticate(db, body.email, body.password)
    if not user:
        auth_service.record_failed_login(body.email)
        log_event("api_login_failure", details={"email": body.email})
        raise HTTPException(status_code=401, detail="Nieprawidlowe dane logowania")
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


@router.post("/refresh", response_model=RefreshResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    """Refresh a token that is close to expiry but still valid.

    Tokens become eligible for refresh in the last 25% of their lifetime.
    """
    new_token = auth_service.refresh_token(db, body.token)
    if not new_token:
        raise HTTPException(
            status_code=401,
            detail="Token nie kwalifikuje sie do odswiezenia",
        )
    return RefreshResponse(token=new_token)
