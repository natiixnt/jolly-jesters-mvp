import json

import httpx
import pytest

from app.core.config import settings
from app.utils import allegro_scraper_http
from app.utils.allegro_scraper_http import fetch_via_http_scraper


@pytest.mark.anyio
async def test_fetch_http_no_results_listing_state(monkeypatch):
    monkeypatch.setattr(settings, "proxy_list_raw", "")

    payload = {"__listing_StoreState": {"items": {"elements": []}}}
    html = f'<script data-serialize-box-id="listing">{json.dumps(payload)}</script>'

    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(allegro_scraper_http.httpx, "AsyncClient", client_factory)

    result = await fetch_via_http_scraper("1234567890123")
    assert result.is_not_found is True
    assert result.is_temporary_error is False


@pytest.mark.anyio
async def test_fetch_http_no_results_text(monkeypatch):
    monkeypatch.setattr(settings, "proxy_list_raw", "")

    html = "<html><body>Brak wynik\u00f3w dla zapytania</body></html>"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(allegro_scraper_http.httpx, "AsyncClient", client_factory)

    result = await fetch_via_http_scraper("1234567890123")
    assert result.is_not_found is True
    assert result.is_temporary_error is False


@pytest.mark.anyio
async def test_fetch_http_blocked_page(monkeypatch):
    monkeypatch.setattr(settings, "proxy_list_raw", "")

    html = "<html><body>captcha-delivery.com</body></html>"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))

    orig_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(allegro_scraper_http.httpx, "AsyncClient", client_factory)

    result = await fetch_via_http_scraper("1234567890123")
    assert result.blocked is True
    assert result.is_temporary_error is True
    assert result.is_not_found is False
