from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)

_jwt_secret_raw = os.getenv("JWT_SECRET", "")
if not _jwt_secret_raw:
    logger.warning("JWT_SECRET not set - using random secret (tokens will not survive restart)")
    _jwt_secret_raw = secrets.token_hex(32)
JWT_SECRET = _jwt_secret_raw
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "24"))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if ":" not in stored:
        return False
    salt, expected_hex = stored.split(":", 1)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return hmac.compare_digest(h.hex(), expected_hex)


def create_tenant(db: Session, name: str, slug: str, plan: str = "free") -> Tenant:
    tenant = Tenant(name=name, slug=slug, plan=plan)
    db.add(tenant)
    db.flush()
    return tenant


def create_user(
    db: Session,
    tenant_id: UUID,
    email: str,
    password: str,
    display_name: str | None = None,
    role: str = "member",
) -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError(f"User with email {email} already exists")
    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, email: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user


def issue_token(user: User) -> str:
    """Simple HMAC-based token: user_id:tenant_id:timestamp:signature"""
    ts = str(int(time.time()))
    payload = f"{user.id}:{user.tenant_id}:{ts}"
    sig = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def validate_token(db: Session, token: str) -> Optional[User]:
    parts = token.split(":")
    if len(parts) != 4:
        return None
    user_id_str, tenant_id_str, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return None

    if time.time() - ts > TOKEN_TTL_HOURS * 3600:
        return None

    payload = f"{user_id_str}:{tenant_id_str}:{ts_str}"
    expected = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        return None

    return db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()


def get_tenant(db: Session, tenant_id: UUID) -> Optional[Tenant]:
    return db.query(Tenant).filter(Tenant.id == tenant_id, Tenant.is_active.is_(True)).first()
