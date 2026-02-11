import asyncio
from decimal import Decimal

from app.utils import allegro_scraper_client as scraper_client
from app.utils.allegro_scraper_client import _derive_price, _derive_sold_count, fetch_via_allegro_scraper


def test_derive_price_filters_null_and_zero():
    payload = {
        "products": [
            {"price": {"amount": None}},
            {"price": {"amount": 0}},
            {"price": {"amount": "10"}},
            {"price": {"amount": "20"}},
            {"price": {"amount": "30"}},
        ]
    }
    assert _derive_price(payload) == Decimal("20")


def test_derive_price_returns_none_when_no_valid_prices():
    payload = {
        "products": [
            {"price": {"amount": None}},
            {"price": {"amount": 0}},
            {"price": {}},
            {},
        ]
    }
    assert _derive_price(payload) is None


def test_derive_sold_count_uses_all_offers():
    payload = {
        "products": [
            {"recentSalesCount": 1, "price": {"amount": "10"}},
            {"recentSalesCount": 9},
            {"recentSalesCount": None, "price": {"amount": "20"}},
        ]
    }
    assert _derive_sold_count(payload) == 9


def test_fetch_via_allegro_scraper_can_force_no_results(monkeypatch):
    monkeypatch.setenv("SCRAPER_FORCE_NO_RESULTS_EANS", "5909999999999")

    class _UnexpectedClient:
        async def __aenter__(self):
            raise AssertionError("_http_client should not be used for forced no_results")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scraper_client, "_http_client", lambda: _UnexpectedClient())

    result = asyncio.run(fetch_via_allegro_scraper("5909999999999"))
    assert result.status == "no_results"
    assert result.is_not_found is True
    assert result.price is None
    assert result.sold_count is None
