from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.services import auth_service


@dataclass
class CurrentUser:
    user: User
    tenant: Tenant

    @property
    def user_id(self) -> UUID:
        return self.user.id

    @property
    def tenant_id(self) -> UUID:
        return self.tenant.id


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[CurrentUser]:
    """Extract user from Bearer token or jj_api_token cookie. Returns None if no multi-tenant auth."""
    # Try Bearer token
    auth_header = request.headers.get("authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    # Try cookie
    if not token:
        token = request.cookies.get("jj_api_token")
    if not token:
        return None

    user = auth_service.validate_token(db, token)
    if not user:
        return None

    tenant = auth_service.get_tenant(db, user.tenant_id)
    if not tenant:
        return None

    return CurrentUser(user=user, tenant=tenant)
