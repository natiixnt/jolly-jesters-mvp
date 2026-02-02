import logging
import time
from pathlib import Path
import base64

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import get_db
from app.utils.local_scraper_client import check_local_scraper_health


FAILED_AUTH: dict[str, list[float]] = {}


app = FastAPI(
    title="Jolly Jesters MVP",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _unauthorized(retry_after: int | None = None):
    return HTMLResponse(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Restricted"'},
        content="Unauthorized",
    )


@app.middleware("http")
async def enforce_basic_auth(request: Request, call_next):
    """Protect all routes with Basic Auth + simple brute-force limiter."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_seconds = 600  # 10 minutes
    fail_limit = 8
    # purge old entries
    if client_ip in FAILED_AUTH:
        FAILED_AUTH[client_ip] = [ts for ts in FAILED_AUTH[client_ip] if now - ts <= window_seconds]
        if len(FAILED_AUTH[client_ip]) >= fail_limit:
            return _unauthorized()
    expected_user = settings.ui_basic_auth_user or "admin"
    expected_pass = settings.ui_basic_auth_password or "1234"

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("basic "):
        return _unauthorized()
    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        provided_user, provided_pass = decoded.split(":", 1)
    except Exception:
        FAILED_AUTH.setdefault(client_ip, []).append(now)
        logger.warning("AUTH_FAIL parse ip=%s path=%s", client_ip, request.url.path)
        return _unauthorized()
    if provided_user != expected_user or provided_pass != expected_pass:
        FAILED_AUTH.setdefault(client_ip, []).append(now)
        logger.warning("AUTH_FAIL creds ip=%s path=%s", client_ip, request.url.path)
        return _unauthorized()

    if client_ip in FAILED_AUTH:
        FAILED_AUTH.pop(client_ip, None)

    return await call_next(request)


@app.on_event("startup")
def _log_local_scraper_config() -> None:
    logger.info(
        "LOCAL_SCRAPER_CONFIG enabled=%s url=%s",
        settings.LOCAL_SCRAPER_ENABLED,
        settings.LOCAL_SCRAPER_URL,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def healthcheck(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    local_scraper = check_local_scraper_health()
    return {"status": "ok", "local_scraper": local_scraper}
