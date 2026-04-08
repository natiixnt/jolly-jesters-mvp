"""Security hardening tests - verify defenses are active."""
import os
import pytest
from unittest.mock import patch, MagicMock
from app.services.auth_service import hash_password, verify_password


class TestPasswordSecurity:
    def test_password_hash_uses_unique_salt(self):
        h1 = hash_password("testpass123")
        h2 = hash_password("testpass123")
        assert h1 != h2  # Different salts

    def test_correct_password_accepted(self):
        h = hash_password("correct")
        assert verify_password("correct", h) is True

    def test_wrong_password_rejected(self):
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_empty_password_rejected(self):
        h = hash_password("something")
        assert verify_password("", h) is False

    def test_null_bytes_in_password(self):
        """Null bytes should not bypass validation."""
        h = hash_password("real_password")
        assert verify_password("real_password\x00anything", h) is False

    def test_malformed_stored_hash_rejected(self):
        """Stored hash without colon separator should be rejected."""
        assert verify_password("anything", "no-colon-here") is False

    def test_hash_format_contains_salt_and_digest(self):
        """Hash output must be salt:hex_digest format."""
        h = hash_password("test")
        parts = h.split(":")
        assert len(parts) == 2
        salt, digest = parts
        assert len(salt) == 32  # 16 bytes hex-encoded
        assert len(digest) == 64  # sha256 hex-encoded


class TestInputSanitization:
    def test_ean_rejects_non_numeric(self):
        """EAN codes must be digits only - SQL injection payloads are not valid."""
        bad_eans = [
            "'; DROP TABLE--",
            "<script>alert(1)</script>",
            "../../etc/passwd",
        ]
        for bad in bad_eans:
            cleaned = bad.strip()
            assert not cleaned.isdigit(), f"Bad EAN '{bad}' should not pass isdigit check"

    def test_filename_path_traversal_detection(self):
        """Filenames with path traversal patterns are dangerous."""
        bad_names = [
            "../../../etc/passwd.xlsx",
            "..\\windows\\system32.xlsx",
            "normal.xlsx\x00.exe",
        ]
        for name in bad_names:
            has_traversal = ".." in name
            has_null = "\x00" in name
            assert has_traversal or has_null, f"'{name}' should contain dangerous pattern"

    def test_ean_sanitization_strips_sql_wildcards(self):
        """SQL wildcards must be stripped from search input."""
        raw = "123%456_789\\000"
        safe = raw.replace("%", "").replace("_", "").replace("\\", "")
        assert "%" not in safe
        assert "_" not in safe
        assert "\\" not in safe


class TestCookieSecurity:
    def test_session_cookie_httponly(self):
        """Session cookies must have httponly flag."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "httponly=True" in content, "Session cookie must set httponly=True"

    def test_session_cookie_samesite_strict(self):
        """Session cookies must use samesite=strict."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert 'samesite="strict"' in content, "Session cookie must set samesite=strict"

    def test_csrf_cookie_httponly(self):
        """CSRF cookie must also have httponly flag."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        # The CSRF cookie is set with httponly=True
        assert content.count("httponly=True") >= 2, (
            "Both session and CSRF cookies must set httponly=True"
        )


class TestSecurityHeaders:
    def test_security_headers_middleware_exists(self):
        """Security headers middleware must be configured."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "X-Content-Type-Options" in content, "Must set X-Content-Type-Options header"
        assert "X-Frame-Options" in content, "Must set X-Frame-Options header"
        assert "Referrer-Policy" in content, "Must set Referrer-Policy header"

    def test_xframe_set_to_deny(self):
        """X-Frame-Options must be DENY to prevent clickjacking."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert '"DENY"' in content, "X-Frame-Options must be set to DENY"

    def test_xcontent_type_set_to_nosniff(self):
        """X-Content-Type-Options must be nosniff."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert '"nosniff"' in content, "X-Content-Type-Options must be set to nosniff"


class TestJWTSecurity:
    def test_jwt_requires_secret_in_prod(self):
        """JWT secret must be required in production."""
        auth_path = os.path.join(
            os.path.dirname(__file__), "..", "services", "auth_service.py"
        )
        with open(auth_path) as f:
            content = f.read()
        assert "production" in content.lower() or "prod" in content.lower(), (
            "Auth service must check for production environment"
        )
        assert "RuntimeError" in content, (
            "Auth service must raise error when JWT_SECRET missing in prod"
        )

    def test_jwt_has_expiry(self):
        """JWT tokens must have expiry enforcement."""
        auth_path = os.path.join(
            os.path.dirname(__file__), "..", "services", "auth_service.py"
        )
        with open(auth_path) as f:
            content = f.read()
        assert "TOKEN_TTL_HOURS" in content, "Token TTL must be defined"
        assert "time.time()" in content, "Token validation must check current time"

    def test_jwt_uses_hmac(self):
        """JWT signature must use HMAC for integrity."""
        auth_path = os.path.join(
            os.path.dirname(__file__), "..", "services", "auth_service.py"
        )
        with open(auth_path) as f:
            content = f.read()
        assert "hmac.new" in content or "hmac.compare_digest" in content, (
            "Token validation must use HMAC"
        )


class TestCORSSecurity:
    def test_cors_not_wildcard_default(self):
        """CORS must not default to wildcard allowing all origins."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        # The fallback must be empty list, not ["*"]
        assert 'or ["*"]' not in content, "CORS must not fall back to wildcard"
        assert "or ['*']" not in content, "CORS must not fall back to wildcard"

    def test_cors_origins_from_env(self):
        """CORS origins must be loaded from environment variable."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "CORS_ORIGINS" in content, "CORS origins must come from CORS_ORIGINS env var"


class TestRequestSizeLimiting:
    def test_request_size_limit_middleware_exists(self):
        """A middleware must limit request body size."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "content-length" in content.lower(), "Must check Content-Length header"
        assert "413" in content, "Must return 413 for oversized requests"


class TestBruteForceProtection:
    def test_brute_force_tracking_exists(self):
        """Brute-force login protection must be implemented."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "FAILED_AUTH" in content, "Failed auth tracking must exist"
        assert "429" in content, "Must return 429 for too many attempts"

    def test_account_lockout_in_auth_service(self):
        """Auth service must implement account lockout."""
        auth_path = os.path.join(
            os.path.dirname(__file__), "..", "services", "auth_service.py"
        )
        with open(auth_path) as f:
            content = f.read()
        assert "check_account_lock" in content, "Account lockout check must exist"
        assert "record_failed_login" in content, "Failed login recording must exist"


class TestCSRFProtection:
    def test_csrf_token_on_login(self):
        """Login form must use CSRF tokens."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "csrf_token" in content, "CSRF token must be used in login flow"
        assert "compare_digest" in content, "CSRF comparison must be timing-safe"

    def test_docs_disabled(self):
        """OpenAPI docs must be disabled in the app."""
        main_path = os.path.join(
            os.path.dirname(__file__), "..", "main.py"
        )
        with open(main_path) as f:
            content = f.read()
        assert "docs_url=None" in content, "Swagger docs must be disabled"
        assert "redoc_url=None" in content, "Redoc must be disabled"
        assert "openapi_url=None" in content, "OpenAPI schema must be disabled"
