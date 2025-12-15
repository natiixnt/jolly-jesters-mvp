import logging
import os
import sys
import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.exc import OperationalError
import sqlalchemy as sa

# ensure project root (backend/) is on PYTHONPATH for both docker and host executions
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import settings
from app.db.session import Base  # noqa: F401
import app.models  # noqa: F401  # ensure models are imported for metadata

# target metadata used for autogenerate
target_metadata = Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

# Always use the same DB URL as the app
config.set_main_option("sqlalchemy.url", settings.db_url)

ALEMBIC_MAX_RETRIES = max(1, int(os.getenv("ALEMBIC_MAX_RETRIES", "5")))
ALEMBIC_RETRY_DELAY = max(0.5, float(os.getenv("ALEMBIC_RETRY_DELAY", "2.0")))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connect_kwargs = dict(config.get_section(config.config_ini_section, {}) or {})
    connect_kwargs["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    for attempt in range(1, ALEMBIC_MAX_RETRIES + 1):
        connectable = engine_from_config(
            connect_kwargs,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        try:
            with connectable.connect() as connection:
                # Ensure alembic_version can store long revision ids (ours exceed 32 chars)
                try:
                    with connection.begin():
                        # Create alembic_version upfront if missing, using VARCHAR(64)
                        exists = connection.execute(
                            sa.text("SELECT to_regclass('alembic_version')")
                        ).scalar()
                        if not exists:
                            connection.execute(
                                sa.text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
                            )
                            logger.info("Created alembic_version with VARCHAR(64)")

                        current_len = connection.execute(
                            sa.text(
                                """
                                SELECT character_maximum_length
                                FROM information_schema.columns
                                WHERE table_name = 'alembic_version'
                                  AND column_name = 'version_num'
                                  AND table_schema = current_schema()
                                """
                            )
                        ).scalar()
                        if current_len is not None and current_len < 64:
                            connection.execute(
                                sa.text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
                            )
                            logger.info("Expanded alembic_version.version_num to VARCHAR(64)")
                except Exception as exc:  # pragma: no cover - defensive fallback
                    logger.warning("Could not adjust alembic_version.version_num length: %s", exc)

                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    version_table_column_type=sa.String(length=64),
                )

                with context.begin_transaction():
                    context.run_migrations()

            logger.info("Migrations applied successfully on attempt %s", attempt)
            break
        except OperationalError as exc:
            if attempt >= ALEMBIC_MAX_RETRIES:
                logger.error("Database connection failed after %s attempts", attempt)
                raise

            wait_time = ALEMBIC_RETRY_DELAY * attempt
            logger.warning(
                "Database not ready (attempt %s/%s): %s. Retrying in %.1f seconds...",
                attempt,
                ALEMBIC_MAX_RETRIES,
                exc,
                wait_time,
            )
            time.sleep(wait_time)
        finally:
            connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
