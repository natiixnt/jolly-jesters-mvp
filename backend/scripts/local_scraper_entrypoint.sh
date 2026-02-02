#!/usr/bin/env bash
set -euo pipefail

display="${DISPLAY:-:99}"
screen="${XVFB_SCREEN:-1280x800x24}"
user_data_dir="${SELENIUM_USER_DATA_DIR:-/data/chrome-profile}"
display_num="${display#*:}"
display_num="${display_num%%.*}"
lock_file="/tmp/.X${display_num}-lock"
socket_file="/tmp/.X11-unix/X${display_num}"

export DISPLAY="$display"

mkdir -p "$user_data_dir"

if [ -e "$lock_file" ] || [ -e "$socket_file" ]; then
  echo "[local_scraper] Removing stale Xvfb locks for display $display" >&2
  rm -f "$lock_file" "$socket_file"
fi

Xvfb "$display" -screen 0 "$screen" -ac -nolisten tcp &
for _ in $(seq 1 50); do
  if [ -e "$socket_file" ]; then
    break
  fi
  sleep 0.1
done
if [ ! -e "$socket_file" ]; then
  echo "[local_scraper] Xvfb did not start for display $display" >&2
  exit 1
fi

if [ "${LOCAL_SCRAPER_ENABLE_VNC:-0}" = "1" ]; then
  novnc_web="${NOVNC_WEB_DIR:-/opt/novnc}"
  if command -v x11vnc >/dev/null 2>&1 && command -v websockify >/dev/null 2>&1 && [ -d "$novnc_web" ]; then
    x11vnc -display "$display" -forever -shared -rfbport 5900 -nopw -listen 0.0.0.0 &
    websockify --web="$novnc_web" 6080 localhost:5900 &
  else
    echo "[local_scraper] VNC requested but dependencies are missing. Rebuild with WITH_VNC=1." >&2
  fi
fi

# Start proxy forwarder if USE_PROXY_FORWARDER is enabled and SELENIUM_PROXY is set
if [ "${USE_PROXY_FORWARDER:-0}" = "1" ] && [ -n "${SELENIUM_PROXY:-}" ]; then
  # Save original proxy for the forwarder to use as upstream
  export SELENIUM_PROXY_ORIGINAL="$SELENIUM_PROXY"
  echo "[local_scraper] Starting proxy forwarder on 127.0.0.1:8888" >&2
  echo "[local_scraper] Upstream proxy: ${SELENIUM_PROXY_ORIGINAL}" >&2
  python /app/proxy_forwarder.py &
  sleep 1
  # Override SELENIUM_PROXY for the browser to use local forwarder (no auth needed)
  export SELENIUM_PROXY="http://127.0.0.1:8888"
  echo "[local_scraper] Browser will use proxy: $SELENIUM_PROXY (no auth)" >&2
fi

exec python -m uvicorn local_scraper_service:app --host 0.0.0.0 --port "${LOCAL_SCRAPER_PORT:-5050}"
