import logging
import os
import time
from pathlib import Path
import hmac
import base64
from hashlib import sha256
from typing import Optional

from fastapi import Depends, FastAPI, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
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


def _unauthorized(retry_after: int | None = None, too_many: bool = False):
    headers = {"WWW-Authenticate": 'Basic realm="Restricted"'}
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    return HTMLResponse(
        status_code=429 if too_many else 401,
        headers=headers,
        content="Unauthorized",
    )


def _sign_session(timestamp: int) -> str:
    secret = (settings.ui_password or "1234").encode("utf-8")
    msg = str(timestamp).encode("utf-8")
    sig = hmac.new(secret, msg, sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")


def _issue_cookie() -> tuple[str, int]:
    now = int(time.time())
    return f"{now}.{_sign_session(now)}", now


def _validate_cookie(token: str) -> bool:
    if not token or "." not in token:
        return False
    ts_str, sig = token.split(".", 1)
    try:
        ts = int(ts_str)
    except Exception:
        return False
    ttl_seconds = max(1, settings.ui_session_ttl_hours or 24) * 3600
    if time.time() - ts > ttl_seconds:
        return False
    expected = _sign_session(ts)
    return hmac.compare_digest(sig, expected)


def _is_api_path(path: str) -> bool:
    return path.startswith("/api")


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept


@app.middleware("http")
async def enforce_basic_auth(request: Request, call_next):
    """Protect all routes with cookie-based login; brute-force guard on failures."""
    # Test-mode bypass (used in CI/pytest)
    if os.getenv("UI_AUTH_BYPASS") or os.getenv("PYTEST_CURRENT_TEST"):
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_seconds = 600  # 10 minutes
    fail_limit = 8
    # purge old entries
    if client_ip in FAILED_AUTH:
        FAILED_AUTH[client_ip] = [ts for ts in FAILED_AUTH[client_ip] if now - ts <= window_seconds]
        if len(FAILED_AUTH[client_ip]) >= fail_limit:
            return _unauthorized(retry_after=window_seconds, too_many=True)
    path = request.url.path or "/"
    if path.startswith("/static") or path.startswith("/favicon"):
        # still require cookie
        pass
    if path.startswith("/login"):
        return await call_next(request)

    session_cookie = request.cookies.get("jj_session")
    if session_cookie and _validate_cookie(session_cookie):
        if client_ip in FAILED_AUTH:
            FAILED_AUTH.pop(client_ip, None)
        return await call_next(request)

    # unauthorized
    if _is_api_path(path):
        return _unauthorized()
    if _wants_html(request):
        return RedirectResponse(url="/login", status_code=302)
    return _unauthorized()


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


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    window_seconds = 600
    fail_limit = 8
    if client_ip in FAILED_AUTH:
        FAILED_AUTH[client_ip] = [ts for ts in FAILED_AUTH[client_ip] if time.time() - ts <= window_seconds]
        if len(FAILED_AUTH[client_ip]) >= fail_limit:
            return templates.TemplateResponse("login.html", {"request": request, "error": "Too many attempts, try later."}, status_code=429)
    expected = settings.ui_password or "1234"
    if password != expected:
        FAILED_AUTH.setdefault(client_ip, []).append(time.time())
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"}, status_code=401)
    token, ts = _issue_cookie()
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="jj_session",
        value=token,
        httponly=True,
        max_age=max(1, (settings.ui_session_ttl_hours or 24) * 3600),
        samesite="lax",
        secure=False,
        path="/",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("jj_session", path="/")
    return response
