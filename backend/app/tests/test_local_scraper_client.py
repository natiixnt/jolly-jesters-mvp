import pytest
import httpx
from decimal import Decimal

from app.core.config import settings
from app.utils import local_scraper_client
from app.utils.local_scraper_client import fetch_via_local_scraper, check_local_scraper_health


@pytest.mark.anyio
async def test_fetch_local_scraper_success(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050/scrape")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/scrape"
        return httpx.Response(200, json={"price": "12.5", "sold_count": 3})

    transport = httpx.MockTransport(handler)

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(local_scraper_client.httpx, "AsyncClient", client_factory)

    result = await fetch_via_local_scraper("5901234567890")
    assert result.price == Decimal("12.5")
    assert result.sold_count == 3
    assert result.is_temporary_error is False
    assert result.raw_payload["url"].endswith("/scrape")


@pytest.mark.anyio
async def test_fetch_local_scraper_timeout(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    transport = httpx.MockTransport(handler)

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(local_scraper_client.httpx, "AsyncClient", client_factory)

    result = await fetch_via_local_scraper("5901234567890")
    assert result.is_temporary_error is True
    assert "error" in result.raw_payload
    assert result.raw_payload["url"] == "http://host.docker.internal:5050/scrape"
    assert result.raw_payload.get("error_type") == "timeout"


@pytest.mark.anyio
async def test_fetch_local_scraper_http_error(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "bad gateway"})

    transport = httpx.MockTransport(handler)

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(local_scraper_client.httpx, "AsyncClient", client_factory)

    result = await fetch_via_local_scraper("5901234567890")
    assert result.is_temporary_error is True
    assert result.price is None
    assert result.raw_payload.get("error_type") == "http_error"


def test_health_check_success(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050/scrape")

    def fake_get(url, timeout):
        assert url.endswith("/health")
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(local_scraper_client.httpx, "get", fake_get)

    payload = check_local_scraper_health(timeout=1)
    assert payload["status"] == "ok"


def test_health_check_failure(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050")

    def fake_get(url, timeout):
        return httpx.Response(500, text="fail")

    monkeypatch.setattr(local_scraper_client.httpx, "get", fake_get)

    with pytest.raises(RuntimeError):
        check_local_scraper_health(timeout=1)
