from sqlalchemy.orm import declarative_base

# Shared declarative base for all ORM models
Base = declarative_base()

# Import models for metadata discovery (keep at end to avoid circular imports)
from app import models  # noqa: F401,E402
