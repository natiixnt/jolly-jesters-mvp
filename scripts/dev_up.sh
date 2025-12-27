#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${LOCAL_SCRAPER_ENV_FILE:-$ROOT/backend/.env}"
LOCAL_SCRAPER_UPDATE_ENV="${LOCAL_SCRAPER_UPDATE_ENV:-1}"

log() {
  echo "[dev_up] $*"
}

update_env_file() {
  local url="$1"
  if [ ! -f "$ENV_FILE" ]; then
    log "Env file not found at $ENV_FILE; skipping update."
    return 0
  fi
  python3 - <<PY
from pathlib import Path

env_path = Path(r"$ENV_FILE")
lines = env_path.read_text().splitlines()
url = "$url"

def upsert(key: str, value: str) -> None:
    prefix = f"{key}="
    for idx, line in enumerate(lines):
        if line.startswith(prefix):
            lines[idx] = f"{prefix}{value}"
            return
    lines.append(f"{prefix}{value}")

upsert("LOCAL_SCRAPER_URL", url)
upsert("LOCAL_SCRAPER_ENABLED", "true")
env_path.write_text("\\n".join(lines) + "\\n")
PY
  log "Updated $ENV_FILE with LOCAL_SCRAPER_URL=$url"
}

if [ "$LOCAL_SCRAPER_UPDATE_ENV" = "1" ]; then
  if [ -n "${LOCAL_SCRAPER_URL:-}" ]; then
    update_env_file "$LOCAL_SCRAPER_URL"
  elif [ -n "${LOCAL_SCRAPER_PORT:-}" ]; then
    update_env_file "http://host.docker.internal:${LOCAL_SCRAPER_PORT}"
  fi
fi

exec docker compose up --build "$@"
