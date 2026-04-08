from __future__ import annotations

import logging
import os
import secrets
import time

from app.core.logging_config import setup_logging
setup_logging()
from pathlib import Path
import hmac
import base64
from hashlib import sha256
from typing import Optional

from fastapi import Depends, FastAPI, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.services.audit_service import log_event
from app.utils.allegro_scraper_client import check_scraper_health


# NOTE: in-memory brute-force tracker - resets on restart and not shared across
# workers. For production, consider Redis-backed rate limiting.
FAILED_AUTH: dict[str, list[float]] = {}

app = FastAPI(
    title="Jolly Jesters MVP",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
logger = logging.getLogger(__name__)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    logger.error("Unhandled exception: %s\n%s", str(exc), traceback.format_exc())
    # Never leak stack traces to client
    return JSONResponse(
        status_code=500,
        content={"detail": "Wewnetrzny blad serwera. Skontaktuj sie z administratorem."},
    )

_cors_origins = os.getenv("CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or [],
    allow_credentials=bool(_cors_origins),
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)

@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Reject requests larger than 50 MB based on Content-Length header."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 52_428_800:  # 50 MB
        return JSONResponse(status_code=413, content={"detail": "Plik jest za duzy"})
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'"
    )
    return response


if not settings.ui_password or settings.ui_password == "1234":
    if os.getenv("ENVIRONMENT", "dev").lower() in ("production", "prod"):
        raise RuntimeError("UI_PASSWORD must be set to a strong value in production")
    logger.warning("UI_PASSWORD not set or still default '1234' - INSECURE, change for production!")

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


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "dev").lower() in ("production", "prod")


def _cookie_secure() -> bool:
    """Use Secure flag in production or when explicitly enabled."""
    if _is_production():
        return True
    return os.getenv("COOKIE_SECURE", "").lower() in ("1", "true", "yes")


def _is_api_path(path: str) -> bool:
    return path.startswith("/api")


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept


@app.middleware("http")
async def enforce_basic_auth(request: Request, call_next):
    """Protect all routes with cookie-based login; brute-force guard on failures."""
    # Test-mode bypass (used in CI/pytest)
    if os.getenv("PYTEST_CURRENT_TEST"):
        return await call_next(request)

    # prefer real IP from trusted reverse proxy
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.headers.get("x-real-ip", "")
        or (request.client.host if request.client else "unknown")
    )
    now = time.time()
    window_seconds = 600  # 10 minutes
    fail_limit = 5
    # emergency cleanup to prevent OOM
    if len(FAILED_AUTH) > 10000:
        FAILED_AUTH.clear()
    # purge old entries
    if client_ip in FAILED_AUTH:
        FAILED_AUTH[client_ip] = [ts for ts in FAILED_AUTH[client_ip] if now - ts <= window_seconds]
        if len(FAILED_AUTH[client_ip]) >= fail_limit:
            return _unauthorized(retry_after=window_seconds, too_many=True)
    path = request.url.path or "/"
    if path.startswith("/healthz"):
        return await call_next(request)
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


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def healthcheck(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    scraper = check_scraper_health()
    scraper_status = scraper.get("status", "error")
    # redis check
    redis_ok = False
    try:
        import redis
        r = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
        redis_ok = r.ping()
    except Exception:
        pass
    return {"status": "ok", "scraper": scraper_status, "redis": "ok" if redis_ok else "error"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    csrf_token = secrets.token_hex(32)
    response = templates.TemplateResponse(
        "login.html", {"request": request, "error": None, "csrf_token": csrf_token}
    )
    response.set_cookie(
        "csrf_token",
        csrf_token,
        httponly=True,
        samesite="lax",
        max_age=3600,
        secure=_cookie_secure(),
        path="/",
    )
    return response


@app.post("/login", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def login_submit(request: Request):
    form_data = await request.form()
    password = str(form_data.get("password", ""))

    # --- CSRF validation (skip in dev for easier testing) ---
    csrf_from_form = str(form_data.get("csrf_token", ""))
    csrf_from_cookie = request.cookies.get("csrf_token", "")
    _is_prod = os.getenv("ENVIRONMENT", "dev").lower() in ("production", "prod")
    if _is_prod and (
        not csrf_from_form
        or not csrf_from_cookie
        or not hmac.compare_digest(csrf_from_form, csrf_from_cookie)
    ):
        csrf_token = secrets.token_hex(32)
        resp = templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "CSRF validation failed", "csrf_token": csrf_token},
            status_code=403,
        )
        resp.set_cookie(
            "csrf_token", csrf_token, httponly=True, samesite="lax",
            max_age=3600, secure=_cookie_secure(), path="/",
        )
        return resp

    # --- Brute-force guard ---
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.headers.get("x-real-ip", "")
        or (request.client.host if request.client else "unknown")
    )
    window_seconds = 600
    fail_limit = 5
    if client_ip in FAILED_AUTH:
        FAILED_AUTH[client_ip] = [ts for ts in FAILED_AUTH[client_ip] if time.time() - ts <= window_seconds]
        if len(FAILED_AUTH[client_ip]) >= fail_limit:
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Too many attempts, try later.", "csrf_token": ""},
                status_code=429,
            )
    expected = settings.ui_password
    if not expected:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "UI_PASSWORD not configured on server", "csrf_token": ""},
            status_code=503,
        )
    if not hmac.compare_digest(password, expected):
        FAILED_AUTH.setdefault(client_ip, []).append(time.time())
        log_event("login_failure", ip=client_ip, details={"reason": "invalid_password"})
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid password", "csrf_token": ""},
            status_code=401,
        )

    # --- Issue session cookie and clear CSRF cookie (session fixation protection) ---
    log_event("login_success", ip=client_ip)
    token, ts = _issue_cookie()
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="jj_session",
        value=token,
        httponly=True,
        max_age=max(1, (settings.ui_session_ttl_hours or 24) * 3600),
        samesite="strict",
        secure=_cookie_secure(),
        path="/",
    )
    response.delete_cookie("csrf_token", path="/")
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("jj_session", path="/")
    response.delete_cookie("csrf_token", path="/")
    return response
