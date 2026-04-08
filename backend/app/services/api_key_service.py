from __future__ import annotations

import hashlib
import json
import secrets
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.api_key import APIKey, SCOPE_READ_ONLY, VALID_SCOPES

logger = logging.getLogger(__name__)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Per-API-key rate limiting (in-memory)
# ---------------------------------------------------------------------------
_api_key_usage: dict[str, list[float]] = {}  # key_hash -> [timestamps]


def check_api_key_rate(key_hash: str, max_per_minute: int = 60) -> bool:
    """Check if API key has exceeded rate limit.

    Returns True if request is allowed, False if rate limit exceeded.
    """
    now = time.time()
    window = 60  # 1 minute

    # Clean stale entries periodically to prevent unbounded growth
    if len(_api_key_usage) > 10000:
        stale = [k for k, ts in _api_key_usage.items() if not ts or now - ts[-1] > 300]
        for k in stale:
            del _api_key_usage[k]

    if key_hash not in _api_key_usage:
        _api_key_usage[key_hash] = []

    # Clean old entries
    _api_key_usage[key_hash] = [t for t in _api_key_usage[key_hash] if now - t < window]

    if len(_api_key_usage[key_hash]) >= max_per_minute:
        return False

    _api_key_usage[key_hash].append(now)
    return True


def validate_scopes(scopes: list[str]) -> list[str]:
    """Validate and return only known scopes. Raises ValueError for invalid scopes."""
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise ValueError(f"Invalid scopes: {', '.join(sorted(invalid))}")
    return scopes


def create_api_key(
    db: Session,
    tenant_id: str,
    name: str,
    user_id: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    scopes: Optional[list[str]] = None,
) -> Tuple[APIKey, str]:
    """Create a new API key. Returns (key_record, raw_key). Raw key is only shown once."""
    raw_key = f"jj_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:11]  # "jj_" + first 8 chars

    if scopes is not None:
        scopes = validate_scopes(scopes)
    else:
        scopes = SCOPE_READ_ONLY[:]

    record = APIKey(
        tenant_id=tenant_id,
        user_id=user_id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        scopes=json.dumps(scopes),
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record, raw_key


def validate_api_key(
    db: Session,
    raw_key: str,
    required_scope: Optional[str] = None,
) -> Optional[APIKey]:
    """Validate an API key. Returns the key record if valid, None otherwise.

    If required_scope is provided, also checks that the key has that scope.
    Rate limiting is enforced per key hash.
    """
    key_hash = _hash_key(raw_key)

    # Check rate limit before DB lookup
    if not check_api_key_rate(key_hash):
        logger.warning("API key rate limit exceeded: %s...", key_hash[:12])
        return None

    key = (
        db.query(APIKey)
        .filter(APIKey.key_hash == key_hash, APIKey.is_active == True)
        .first()
    )
    if not key:
        return None

    if key.expires_at and key.expires_at < datetime.now(timezone.utc):
        key.is_active = False
        db.commit()
        return None

    # Check scope if required
    if required_scope and not key.has_scope(required_scope):
        logger.warning(
            "API key %s lacks required scope '%s' (has: %s)",
            key.key_prefix,
            required_scope,
            key.get_scopes(),
        )
        return None

    key.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return key


def list_keys(db: Session, tenant_id: str) -> List[APIKey]:
    return (
        db.query(APIKey)
        .filter(APIKey.tenant_id == tenant_id)
        .order_by(APIKey.created_at.desc())
        .all()
    )


def revoke_key(db: Session, tenant_id: str, key_id: int) -> bool:
    key = (
        db.query(APIKey)
        .filter(APIKey.id == key_id, APIKey.tenant_id == tenant_id)
        .first()
    )
    if not key:
        return False
    key.is_active = False
    db.commit()
    return True
