"""Shared rate limiter instance for the application.

Import ``limiter`` from here in any router module to apply per-endpoint limits
via the ``@limiter.limit()`` decorator.  The limiter is attached to the FastAPI
app in ``app.main``.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
