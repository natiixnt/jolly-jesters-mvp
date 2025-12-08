#!/usr/bin/env bash
set -euo pipefail

# Run alembic migrations. Execute inside the backend container:
#   docker compose exec pilot_backend bash backend/scripts/migrate.sh

alembic upgrade head
