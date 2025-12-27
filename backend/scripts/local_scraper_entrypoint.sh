#!/usr/bin/env bash
set -euo pipefail

display="${DISPLAY:-:99}"
screen="${XVFB_SCREEN:-1280x800x24}"
user_data_dir="${SELENIUM_USER_DATA_DIR:-/data/chrome-profile}"

export DISPLAY="$display"

mkdir -p "$user_data_dir"

Xvfb "$display" -screen 0 "$screen" -ac -nolisten tcp &

if [ "${LOCAL_SCRAPER_ENABLE_VNC:-0}" = "1" ]; then
  novnc_web="${NOVNC_WEB_DIR:-/opt/novnc}"
  if command -v x11vnc >/dev/null 2>&1 && command -v websockify >/dev/null 2>&1 && [ -d "$novnc_web" ]; then
    x11vnc -display "$display" -forever -shared -rfbport 5900 -nopw -listen 0.0.0.0 &
    websockify --web="$novnc_web" 6080 localhost:5900 &
  else
    echo "[local_scraper] VNC requested but dependencies are missing. Rebuild with WITH_VNC=1." >&2
  fi
fi

exec python -m uvicorn local_scraper_service:app --host 0.0.0.0 --port "${LOCAL_SCRAPER_PORT:-5050}"
