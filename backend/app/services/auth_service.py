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

from fastapi import HTTPException

from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Account lockout tracking
# ---------------------------------------------------------------------------
_account_locks: dict[str, tuple[int, float]] = {}  # email -> (fail_count, locked_until)


def check_account_lock(email: str) -> None:
    """Check if account is locked due to too many failed attempts."""
    lock = _account_locks.get(email)
    if lock:
        fail_count, locked_until = lock
        if time.time() < locked_until:
            remaining = int(locked_until - time.time())
            raise HTTPException(
                status_code=429,
                detail=f"Konto zablokowane na {remaining}s po {fail_count} nieudanych probach",
            )
        elif time.time() >= locked_until:
            del _account_locks[email]


def record_failed_login(email: str) -> None:
    """Record a failed login attempt and lock if threshold exceeded."""
    lock = _account_locks.get(email, (0, 0))
    count = lock[0] + 1
    if count >= 5:
        lockout_seconds = min(300 * (2 ** (count - 5)), 3600)  # 5min, 10min, ... max 1hr
        _account_locks[email] = (count, time.time() + lockout_seconds)
    else:
        _account_locks[email] = (count, 0)


def record_successful_login(email: str) -> None:
    """Clear failed attempts on successful login."""
    _account_locks.pop(email, None)


_jwt_secret_raw = os.getenv("JWT_SECRET", "")
if not _jwt_secret_raw:
    if os.getenv("ENVIRONMENT", "dev").lower() in ("production", "prod"):
        raise RuntimeError("JWT_SECRET must be set in production environment")
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
        raise ValueError("Registration failed")
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
    """HMAC-based token with base64-encoded payload to avoid leaking UUIDs.

    Payload format: user_id:tenant_id:iat:jti
    - iat = issued-at unix timestamp
    - jti = unique token identifier for potential blacklisting
    """
    import base64 as b64
    iat = str(int(time.time()))
    jti = secrets.token_hex(8)
    payload = f"{user.id}:{user.tenant_id}:{iat}:{jti}"
    encoded = b64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def validate_token(db: Session, token: str) -> Optional[User]:
    import base64 as b64
    if "." not in token:
        return None
    encoded, sig = token.rsplit(".", 1)

    # decode payload
    padding = 4 - len(encoded) % 4
    if padding != 4:
        encoded += "=" * padding
    try:
        payload = b64.urlsafe_b64decode(encoded).decode()
    except Exception:
        return None

    parts = payload.split(":")
    # Support new 4-part (uid:tid:iat:jti) and legacy 3-part (uid:tid:ts)
    if len(parts) == 4:
        user_id_str, tenant_id_str, ts_str, _jti = parts
    elif len(parts) == 3:
        user_id_str, tenant_id_str, ts_str = parts
    else:
        return None

    try:
        ts = int(ts_str)
    except ValueError:
        return None

    # Enforce token expiry (exp = iat + TTL)
    if time.time() - ts > TOKEN_TTL_HOURS * 3600:
        return None

    expected = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        return None

    return db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()


def get_tenant(db: Session, tenant_id: UUID) -> Optional[Tenant]:
    return db.query(Tenant).filter(Tenant.id == tenant_id, Tenant.is_active.is_(True)).first()
