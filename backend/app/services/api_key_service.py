from __future__ import annotations

import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.api_key import APIKey

logger = logging.getLogger(__name__)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_api_key(
    db: Session,
    tenant_id: str,
    name: str,
    user_id: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> Tuple[APIKey, str]:
    """Create a new API key. Returns (key_record, raw_key). Raw key is only shown once."""
    raw_key = f"jj_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:11]  # "jj_" + first 8 chars

    record = APIKey(
        tenant_id=tenant_id,
        user_id=user_id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record, raw_key


def validate_api_key(db: Session, raw_key: str) -> Optional[APIKey]:
    """Validate an API key. Returns the key record if valid, None otherwise."""
    key_hash = _hash_key(raw_key)
    key = (
        db.query(APIKey)
        .filter(APIKey.key_hash == key_hash, APIKey.is_active == True)
        .first()
    )
    if not key:
        return None

    if key.expires_at and key.expires_at < datetime.now(timezone.utc):
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
