#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() {
  echo "[local_scraper] $*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

browser_version() {
  local chrome_bin="${1:-}"
  if [ -z "$chrome_bin" ]; then
    return 1
  fi
  "$chrome_bin" --version 2>/dev/null | awk '{for(i=1;i<=NF;i++){if($i ~ /^[0-9]+\\./){print $i; exit}}}'
}

detect_chrome_bin() {
  for bin in chromium chromium-browser google-chrome google-chrome-stable; do
    if need_cmd "$bin"; then
      command -v "$bin"
      return 0
    fi
  done
  if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return 0
  fi
  if [ -x "/Applications/Chromium.app/Contents/MacOS/Chromium" ]; then
    echo "/Applications/Chromium.app/Contents/MacOS/Chromium"
    return 0
  fi
  return 1
}

ensure_chromedriver() {
  if need_cmd chromedriver; then
    return 0
  fi

  local chrome_bin
  if ! chrome_bin="$(detect_chrome_bin)"; then
    log "Chrome/Chromium binary not found; cannot fetch chromedriver."
    return 1
  fi

  local version
  version="$(browser_version "$chrome_bin" || true)"
  if [ -z "$version" ]; then
    log "Failed to detect browser version from $chrome_bin."
    return 1
  fi

  local platform
  case "$(uname -s)" in
    Darwin)
      if [ "$(uname -m)" = "arm64" ]; then
        platform="mac-arm64"
      else
        platform="mac-x64"
      fi
      ;;
    Linux)
      platform="linux64"
      ;;
    *)
      log "Unsupported OS for chromedriver download."
      return 1
      ;;
  esac

  local url
  url="$(python3 - <<PY
import json
import sys
from urllib.request import urlopen

version = "$version"
major = version.split(".")[0]
platform = "$platform"

def pick_url(data, version_prefix=None):
    for item in data.get("versions", []):
        ver = item.get("version")
        if not ver:
            continue
        if version_prefix and not ver.startswith(version_prefix + "."):
            continue
        downloads = item.get("downloads", {}).get("chromedriver", [])
        for d in downloads:
            if d.get("platform") == platform:
                return d.get("url")
    return None

def fetch_json(url):
    with urlopen(url) as resp:
        return json.load(resp)

known_url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
latest_url = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"

try:
    known = fetch_json(known_url)
    url = pick_url(known, version_prefix=major)
    if not url:
        url = pick_url(known, version_prefix=None)
    if url:
        print(url)
        sys.exit(0)
except Exception:
    pass

try:
    latest = fetch_json(latest_url)
    stable = latest.get("channels", {}).get("Stable", {})
    downloads = stable.get("downloads", {}).get("chromedriver", [])
    for d in downloads:
        if d.get("platform") == platform:
            print(d.get("url"))
            sys.exit(0)
except Exception:
    pass

sys.exit(1)
PY
)" || true

  if [ -z "$url" ]; then
    log "Failed to resolve chromedriver download URL."
    return 1
  fi

  log "Downloading chromedriver from Chrome-for-Testing..."
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  curl -fsSL "$url" -o "$tmp_dir/chromedriver.zip"
  unzip -q "$tmp_dir/chromedriver.zip" -d "$tmp_dir"
  local driver_path
  driver_path="$(find "$tmp_dir" -name chromedriver -type f | head -n 1)"
  if [ -z "$driver_path" ]; then
    log "chromedriver not found in archive."
    rm -rf "$tmp_dir"
    return 1
  fi
  mkdir -p "$HOME/.local/bin"
  cp "$driver_path" "$HOME/.local/bin/chromedriver"
  chmod +x "$HOME/.local/bin/chromedriver"
  export PATH="$HOME/.local/bin:$PATH"
  rm -rf "$tmp_dir"

  if need_cmd chromedriver; then
    log "chromedriver installed to $HOME/.local/bin/chromedriver"
    return 0
  fi
  return 1
}

ensure_sudo() {
  if [ "$(id -u)" -ne 0 ]; then
    if need_cmd sudo; then
      SUDO="sudo"
    else
      echo "sudo not found; run as root or install sudo." >&2
      exit 1
    fi
  else
    SUDO=""
  fi
}

install_deps_macos() {
  if ! need_cmd brew; then
    log "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -x /opt/homebrew/bin/brew ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
  fi

  log "Installing dependencies via Homebrew..."
  brew update
  if ! need_cmd python3; then
    brew install python
  fi
  if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "/Applications/Chromium.app" ]; then
    brew install --cask google-chrome || brew install --cask chromium
  fi
  if ! need_cmd chromedriver; then
    brew install chromedriver || brew install --cask chromedriver
  fi
}

install_deps_linux() {
  if [ ! -f /etc/os-release ]; then
    echo "Unsupported Linux: /etc/os-release missing." >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  ensure_sudo
  case "${ID:-}" in
    debian|ubuntu)
      log "Installing dependencies via apt..."
      $SUDO apt-get update
      $SUDO apt-get install -y python3 python3-venv python3-pip curl
      $SUDO apt-get install -y chromium chromium-driver \
        || $SUDO apt-get install -y chromium-browser chromium-chromedriver \
        || true
      if ! need_cmd chromedriver; then
        $SUDO apt-get install -y chromium-chromedriver || true
      fi
      if ! need_cmd chromium && ! need_cmd chromium-browser && need_cmd snap; then
        log "Chromium not found via apt; trying snap..."
        $SUDO snap install chromium || true
      fi
      if ! need_cmd chromium && ! need_cmd chromium-browser && ! need_cmd snap; then
        log "snap not found; installing snapd..."
        $SUDO apt-get install -y snapd || true
        if need_cmd snap; then
          log "Installing Chromium via snap..."
          $SUDO snap install chromium || true
        fi
      fi
      ;;
    fedora)
      log "Installing dependencies via dnf..."
      $SUDO dnf install -y python3 python3-pip chromium chromedriver
      ;;
    rhel|centos|rocky|almalinux)
      log "Installing dependencies via dnf/yum (EPEL)..."
      if need_cmd dnf; then
        $SUDO dnf install -y epel-release || true
        $SUDO dnf install -y python3 python3-pip chromium chromedriver || true
      elif need_cmd yum; then
        $SUDO yum install -y epel-release || true
        $SUDO yum install -y python3 python3-pip chromium chromedriver || true
      else
        echo "No dnf/yum found on RHEL-like system." >&2
        exit 1
      fi
      ;;
    arch)
      log "Installing dependencies via pacman..."
      $SUDO pacman -S --noconfirm python python-pip chromium chromedriver
      ;;
    manjaro)
      log "Installing dependencies via pacman (Manjaro)..."
      $SUDO pacman -S --noconfirm python python-pip chromium chromedriver
      ;;
    opensuse*|suse)
      log "Installing dependencies via zypper..."
      $SUDO zypper --non-interactive install python3 python3-pip chromium chromium-driver
      ;;
    alpine)
      log "Installing dependencies via apk..."
      $SUDO apk add --no-cache python3 py3-pip py3-virtualenv chromium chromium-chromedriver curl
      ;;
    *)
      echo "Unsupported Linux distro: ${ID:-unknown}" >&2
      exit 1
      ;;
  esac
}

install_deps() {
  case "$(uname -s)" in
    Darwin)
      install_deps_macos
      ;;
    Linux)
      install_deps_linux
      ;;
    *)
      echo "Unsupported OS: $(uname -s)" >&2
      exit 1
      ;;
  esac
}

ensure_local_bin_in_path() {
  if echo "$PATH" | tr ':' '\n' | grep -qx "$HOME/.local/bin"; then
    return 0
  fi
  if [ -n "${LOCAL_SCRAPER_PERSIST_PATH:-}" ]; then
    local shell_rc=""
    if [ -n "${SHELL:-}" ]; then
      case "$SHELL" in
        */zsh) shell_rc="$HOME/.zshrc" ;;
        */bash) shell_rc="$HOME/.bashrc" ;;
        */fish) shell_rc="$HOME/.config/fish/config.fish" ;;
      esac
    fi
    if [ -z "$shell_rc" ]; then
      shell_rc="$HOME/.profile"
    fi
    if [ -n "$shell_rc" ] && [ -f "$shell_rc" ]; then
      if ! grep -q 'HOME/.local/bin' "$shell_rc"; then
        log "Persisting PATH update in $shell_rc"
        if [[ "$shell_rc" == *"config.fish" ]]; then
          printf '\nset -x PATH $HOME/.local/bin $PATH\n' >> "$shell_rc"
        else
          printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$shell_rc"
        fi
      fi
    fi
  fi
}

install_deps

if [ ! -d ".venv" ]; then
  if ! need_cmd python3; then
    echo "python3 not found after install step." >&2
    exit 1
  fi
  python3 -m venv .venv
fi

ensure_local_bin_in_path

if ! need_cmd chromedriver; then
  log "chromedriver missing; trying Chrome-for-Testing download..."
  if ! ensure_chromedriver; then
    echo "chromedriver not found after install step." >&2
    exit 1
  fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install -r requirements.txt

export SELENIUM_HEADED=true
export SELENIUM_USER_DATA_DIR="${SELENIUM_USER_DATA_DIR:-$HOME/.local-scraper-profile}"
export SELENIUM_PROFILE_DIR="${SELENIUM_PROFILE_DIR:-Default}"

LOCAL_SCRAPER_HOST="${LOCAL_SCRAPER_HOST:-0.0.0.0}"
LOCAL_SCRAPER_PORT="${LOCAL_SCRAPER_PORT:-5050}"
LOCAL_SCRAPER_UPDATE_ENV="${LOCAL_SCRAPER_UPDATE_ENV:-1}"
LOCAL_SCRAPER_ENV_FILE="${LOCAL_SCRAPER_ENV_FILE:-$ROOT/.env}"

if [ "$LOCAL_SCRAPER_HOST" = "127.0.0.1" ] || [ "$LOCAL_SCRAPER_HOST" = "localhost" ]; then
  log "Warning: binding to $LOCAL_SCRAPER_HOST may block Docker access. Use 0.0.0.0 for containers."
fi

if need_cmd lsof; then
  if lsof -nP -iTCP:"$LOCAL_SCRAPER_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    log "Port $LOCAL_SCRAPER_PORT is already in use."
    lsof -nP -iTCP:"$LOCAL_SCRAPER_PORT" -sTCP:LISTEN || true
    log "Pick another port: LOCAL_SCRAPER_PORT=5051 $0"
    exit 1
  fi
else
  python3 - <<PY || exit 1
import socket
host = "$LOCAL_SCRAPER_HOST"
port = int("$LOCAL_SCRAPER_PORT")
sock = socket.socket()
try:
    sock.bind((host, port))
except OSError as exc:
    print(f"Port {port} is already in use: {exc}")
    raise SystemExit(1)
finally:
    sock.close()
PY
fi

if [ "$LOCAL_SCRAPER_UPDATE_ENV" = "1" ]; then
  if [ -f "$LOCAL_SCRAPER_ENV_FILE" ]; then
    python3 - <<PY
from pathlib import Path

env_path = Path(r"$LOCAL_SCRAPER_ENV_FILE")
lines = env_path.read_text().splitlines()
port = int("$LOCAL_SCRAPER_PORT")
url = f"http://host.docker.internal:{port}"

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
    log "Updated $LOCAL_SCRAPER_ENV_FILE with LOCAL_SCRAPER_URL=http://host.docker.internal:${LOCAL_SCRAPER_PORT}"
  else
    log "Env file not found at $LOCAL_SCRAPER_ENV_FILE; skipping update."
  fi
fi

log "Starting local scraper on ${LOCAL_SCRAPER_HOST}:${LOCAL_SCRAPER_PORT}"
log "Docker URL: http://host.docker.internal:${LOCAL_SCRAPER_PORT}"
exec uvicorn local_scraper_service:app --host "$LOCAL_SCRAPER_HOST" --port "$LOCAL_SCRAPER_PORT"
