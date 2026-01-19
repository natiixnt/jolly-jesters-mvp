import httpx
import pytest
from decimal import Decimal

from app.core.config import settings
from app.utils import local_scraper_client
from app.utils.local_scraper_client import check_local_scraper_health, fetch_via_local_scraper


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
async def test_fetch_local_scraper_connect_error(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050")
    monkeypatch.setattr(settings, "scraping_retries", 0)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(local_scraper_client.httpx, "AsyncClient", client_factory)

    result = await fetch_via_local_scraper("5901234567890")
    assert result.is_temporary_error is True
    assert result.error == "network_error"
    assert result.raw_payload.get("error") == "network_error"
    assert result.raw_payload["url"] == "http://host.docker.internal:5050/scrape"
    assert result.raw_payload.get("error_type") == "ConnectError"


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
    assert result.raw_payload.get("status_code") == 502
    assert result.raw_payload.get("error") == "http_502"


@pytest.mark.anyio
async def test_fetch_local_scraper_read_timeout(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050")
    monkeypatch.setattr(settings, "scraping_retries", 0)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    transport = httpx.MockTransport(handler)

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(local_scraper_client.httpx, "AsyncClient", client_factory)

    result = await fetch_via_local_scraper("5901234567890")
    assert result.is_temporary_error is True
    assert result.error == "timeout"
    assert result.raw_payload.get("error") == "timeout"
    assert result.raw_payload.get("error_type") == "ReadTimeout"


def test_health_check_success(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050/scrape")
    monkeypatch.setattr(settings, "local_scraper_enabled", True)

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            assert url.endswith("/health")
            return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(local_scraper_client.httpx, "Client", DummyClient)

    payload = check_local_scraper_health(timeout_seconds=1)
    assert payload["status"] == "ok"
    assert payload["status_code"] == 200


def test_health_check_failure(monkeypatch):
    monkeypatch.setattr(settings, "local_scraper_url", "http://host.docker.internal:5050")
    monkeypatch.setattr(settings, "local_scraper_enabled", True)

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return httpx.Response(500, text="fail")

    monkeypatch.setattr(local_scraper_client.httpx, "Client", DummyClient)

    payload = check_local_scraper_health(timeout_seconds=1)
    assert payload["status"] == "error"
    assert payload["status_code"] == 500
