from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_CANDIDATES = (
    ("app.local_scraper_service", BACKEND_DIR / "app" / "local_scraper_service.py"),
    ("app.services.local_scraper_service", BACKEND_DIR / "app" / "services" / "local_scraper_service.py"),
    ("local_scraper_service", BACKEND_DIR / "local_scraper_service.py"),
)

_last_error: Exception | None = None
app = None
for module_path, module_file in _CANDIDATES:
    if not module_file.exists():
        continue
    try:
        module = importlib.import_module(module_path)
        app = getattr(module, "app", None)
        if app is not None:
            break
    except Exception as exc:
        _last_error = exc
        continue

if app is None:
    raise ImportError(f"Could not import ASGI app from: {', '.join(m for m, _ in _CANDIDATES)}") from _last_error

__all__ = ["app"]
