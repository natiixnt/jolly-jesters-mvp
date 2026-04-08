"""Tests for input validation utilities."""

import pytest

from app.utils.validators import sanitize_string, validate_ean, validate_proxy_url


class TestValidateEan:
    def test_valid_ean13(self):
        assert validate_ean("5901234123457") == "5901234123457"

    def test_valid_ean8(self):
        assert validate_ean("12345678") == "12345678"

    def test_strips_whitespace(self):
        assert validate_ean("  5901234123457  ") == "5901234123457"

    def test_rejects_too_short(self):
        with pytest.raises(ValueError, match="Nieprawidlowy kod EAN"):
            validate_ean("123")

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="Nieprawidlowy kod EAN"):
            validate_ean("ABCDEFGH")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Nieprawidlowy kod EAN"):
            validate_ean("")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="Nieprawidlowy kod EAN"):
            validate_ean("12345678901234")

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="Nieprawidlowy kod EAN"):
            validate_ean("1234-5678")


class TestSanitizeString:
    def test_removes_null_bytes(self):
        assert sanitize_string("hello\x00world") == "helloworld"

    def test_removes_control_chars(self):
        assert sanitize_string("he\x01ll\x7fo") == "hello"

    def test_preserves_newline_tab(self):
        assert sanitize_string("hello\nworld\t!") == "hello\nworld\t!"

    def test_truncates_to_max_length(self):
        long = "a" * 500
        assert len(sanitize_string(long, max_length=100)) == 100

    def test_returns_none_for_empty(self):
        assert sanitize_string("") == ""

    def test_returns_falsy_as_is(self):
        assert sanitize_string(None) is None


class TestValidateProxyUrl:
    def test_valid_http(self):
        assert validate_proxy_url("http://proxy.example.com:8080") == "http://proxy.example.com:8080"

    def test_valid_socks5(self):
        assert validate_proxy_url("socks5://proxy.example.com:1080") == "socks5://proxy.example.com:1080"

    def test_adds_scheme_if_missing(self):
        result = validate_proxy_url("proxy.example.com:8080")
        assert result.startswith("http://")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Pusty URL proxy"):
            validate_proxy_url("")

    def test_rejects_empty_whitespace(self):
        with pytest.raises(ValueError, match="Pusty URL proxy"):
            validate_proxy_url("   ")

    def test_rejects_invalid_scheme(self):
        with pytest.raises(ValueError, match="Nieprawidlowy schemat"):
            validate_proxy_url("ftp://proxy.example.com:8080")

    def test_rejects_no_host(self):
        with pytest.raises(ValueError, match="Brak hosta"):
            validate_proxy_url("http://")
