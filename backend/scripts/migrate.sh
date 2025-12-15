#!/usr/bin/env bash
set -euo pipefail

# Run alembic migrations. Execute inside the backend container:
#   docker compose exec backend bash backend/scripts/migrate.sh
# or run the one-shot service:
#   docker compose run --rm migrations

PROJECT_ROOT="$(cd -- "$(dirname "$0")/.." && pwd)"
cd "${PROJECT_ROOT}"

alembic -c alembic.ini upgrade head
