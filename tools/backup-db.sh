#!/bin/bash
# Database backup script - run via cron or manually
# Usage: ./tools/backup-db.sh [backup_dir]
# Cron example: 0 3 * * * /path/to/tools/backup-db.sh /backups

set -euo pipefail

BACKUP_DIR="${1:-./backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="jolly_jesters_${TIMESTAMP}.sql.gz"

# load env
ENV_FILE="${ENV_FILE:-./backend/.env}"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | grep DB_URL | xargs)
fi

# parse DB_URL or use defaults
DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-mvp}"
DB_NAME="${DB_NAME:-mvpdb}"
DB_PASSWORD="${DB_PASSWORD:-mvp}"

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup -> ${BACKUP_DIR}/${FILENAME}"

PGPASSWORD="$DB_PASSWORD" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-owner \
    --no-privileges \
    | gzip > "${BACKUP_DIR}/${FILENAME}"

SIZE=$(du -h "${BACKUP_DIR}/${FILENAME}" | cut -f1)
echo "[$(date)] Backup complete: ${FILENAME} (${SIZE})"

# cleanup old backups (keep last 30)
ls -t "${BACKUP_DIR}"/jolly_jesters_*.sql.gz 2>/dev/null | tail -n +31 | xargs -r rm -f
echo "[$(date)] Old backups cleaned (keeping last 30)"
