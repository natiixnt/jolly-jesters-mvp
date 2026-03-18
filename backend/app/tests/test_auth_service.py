"""Tests for auth service (password hashing, token issuance)."""

from app.services.auth_service import hash_password, verify_password


def test_hash_and_verify():
    pw = "test-password-123"
    hashed = hash_password(pw)
    assert verify_password(pw, hashed)
    assert not verify_password("wrong", hashed)


def test_different_hashes():
    pw = "same-password"
    h1 = hash_password(pw)
    h2 = hash_password(pw)
    # different salts -> different hashes
    assert h1 != h2
    assert verify_password(pw, h1)
    assert verify_password(pw, h2)


def test_verify_invalid_format():
    assert not verify_password("anything", "no-colon-here")
