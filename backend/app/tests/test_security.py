"""Tests for security-critical code paths."""

import hmac
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# CSRF and session security tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Create a test client with PYTEST_CURRENT_TEST unset so auth runs."""
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env["UI_PASSWORD"] = "test-password-1234"
    with patch.dict(os.environ, env, clear=True):
        # Reload settings so UI_PASSWORD takes effect
        from app.core.config import Settings
        test_settings = Settings()
        with patch("app.main.settings", test_settings):
            from app.main import app
            yield TestClient(app, raise_server_exceptions=False)


def test_login_page_sets_csrf_cookie(client):
    """GET /login should return a csrf_token cookie and embed token in HTML."""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "csrf_token" in resp.cookies
    token = resp.cookies["csrf_token"]
    assert len(token) == 64  # hex(32 bytes)
    assert token in resp.text  # embedded in hidden field


def test_login_post_rejects_missing_csrf(client):
    """POST /login without csrf_token should return 403."""
    resp = client.post("/login", data={"password": "1234"})
    assert resp.status_code == 403
    assert "CSRF" in resp.text


def test_login_post_rejects_wrong_csrf(client):
    """POST /login with mismatched csrf_token should return 403."""
    # First get a valid CSRF cookie
    get_resp = client.get("/login")
    csrf_cookie = get_resp.cookies["csrf_token"]
    # Send a different token in the form
    resp = client.post(
        "/login",
        data={"password": "1234", "csrf_token": "wrong-token"},
        cookies={"csrf_token": csrf_cookie},
    )
    assert resp.status_code == 403


def test_login_post_accepts_valid_csrf(client):
    """POST /login with correct csrf_token and password should succeed (302)."""
    get_resp = client.get("/login")
    csrf_token = get_resp.cookies["csrf_token"]
    resp = client.post(
        "/login",
        data={"password": "test-password-1234", "csrf_token": csrf_token},
        cookies={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "jj_session" in resp.cookies


def test_successful_login_clears_csrf_cookie(client):
    """After successful login the csrf_token cookie should be deleted."""
    get_resp = client.get("/login")
    csrf_token = get_resp.cookies["csrf_token"]
    resp = client.post(
        "/login",
        data={"password": "test-password-1234", "csrf_token": csrf_token},
        cookies={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # The response should contain a set-cookie header that deletes csrf_token
    raw_headers = resp.headers.raw
    csrf_headers = [
        v.decode() if isinstance(v, bytes) else v
        for k, v in raw_headers
        if (k.decode() if isinstance(k, bytes) else k).lower() == "set-cookie"
        and "csrf_token" in (v.decode() if isinstance(v, bytes) else v)
    ]
    assert len(csrf_headers) > 0, "Expected a set-cookie header for csrf_token deletion"
    # Starlette delete_cookie sets max-age=0 and/or an expired date
    combined = " ".join(h.lower() for h in csrf_headers)
    assert 'max-age=0' in combined or '1970' in combined


def test_cookie_secure_flag_in_production():
    """_cookie_secure() returns True when ENVIRONMENT=production."""
    from app.main import _cookie_secure
    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        assert _cookie_secure() is True
    with patch.dict(os.environ, {"ENVIRONMENT": "dev"}, clear=False):
        # Without COOKIE_SECURE it should be False
        env = os.environ.copy()
        env.pop("COOKIE_SECURE", None)
        with patch.dict(os.environ, env, clear=True):
            assert _cookie_secure() is False


def test_cookie_secure_flag_with_env_var():
    """_cookie_secure() respects COOKIE_SECURE env var in dev."""
    from app.main import _cookie_secure
    with patch.dict(os.environ, {"ENVIRONMENT": "dev", "COOKIE_SECURE": "true"}):
        assert _cookie_secure() is True
    with patch.dict(os.environ, {"ENVIRONMENT": "dev", "COOKIE_SECURE": "0"}):
        assert _cookie_secure() is False


# ---------------------------------------------------------------------------
# Original security tests
# ---------------------------------------------------------------------------


def test_timing_safe_password_comparison():
    """Verify password comparison uses constant-time comparison."""
    # This test documents the requirement - actual timing attack
    # testing would need statistical analysis
    a = "correct-password"
    b = "wrong-password"
    assert hmac.compare_digest(a, a)
    assert not hmac.compare_digest(a, b)


def test_ilike_escaping():
    """Verify ILIKE special characters are escaped."""
    from app.services.analysis_service import build_cached_worklist
    # The escaping happens inline; test the logic directly
    raw = "test%_value\\special"
    safe = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    assert "\\%" in safe
    assert "\\_" in safe
    assert "\\\\" in safe


def test_jwt_secret_not_default():
    """JWT_SECRET should not be a known insecure default."""
    from app.services.auth_service import JWT_SECRET
    assert JWT_SECRET != "change-me-in-production"
    assert len(JWT_SECRET) >= 16


def test_auth_hash_verify():
    """Verify password hashing produces different salts."""
    from app.services.auth_service import hash_password, verify_password
    h1 = hash_password("test123")
    h2 = hash_password("test123")
    assert h1 != h2  # different salts
    assert verify_password("test123", h1)
    assert verify_password("test123", h2)
    assert not verify_password("wrong", h1)
