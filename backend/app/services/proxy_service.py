from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from app.core.config import settings
from app.utils.allegro_scraper_client import reload_scraper_proxies


def _target_path() -> Path:
    path = settings.proxies_file
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _parse_lines(data: bytes) -> List[str]:
    text = data.decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Lista proxy jest pusta")
    return lines


def get_metadata() -> Dict:
    path = _target_path()
    if not path.exists():
        return {
            "path": str(path),
            "count": 0,
            "size_bytes": 0,
            "updated_at": None,
            "sample": [],
        }
    stat = path.stat()
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    return {
        "path": str(path),
        "count": len(lines),
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        "sample": lines[:5],
    }


def save_list(data: bytes, reload: bool = True) -> Dict:
    lines = _parse_lines(data)
    path = _target_path()
    payload = "\n".join(lines) + "\n"
    path.write_text(payload, encoding="utf-8")

    meta = get_metadata()
    meta["saved"] = True
    if reload:
        meta["reload"] = reload_scraper_proxies()
    return meta


def reload_proxies() -> Dict:
    return reload_scraper_proxies()
