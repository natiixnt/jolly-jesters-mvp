# Compatibility wrapper for legacy imports.
from app.db.session import SessionLocal, engine, get_db  # noqa: F401
from app.db.base import Base  # noqa: F401
