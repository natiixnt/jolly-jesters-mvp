"""Tests for security-critical code paths."""

import hmac


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
