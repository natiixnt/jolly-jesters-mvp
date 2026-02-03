from decimal import Decimal

import pytest

from app.parsers.allegro_html_scraper import (
    choose_lowest_offer,
    extract_candidate_offer_urls,
    extract_price,
    extract_sold_count,
)
from app.providers.decodo_client import FetchResult


def test_extract_candidate_offer_urls_includes_oferta_and_produkt():
    html = """
    <a href="https://allegro.pl/oferta/abc-123">One</a>
    <a href="/produkt/something?param=1&offerId=XYZ789">Two</a>
    <a href="https://allegro.pl/oferta/abc-123">Duplicate</a>
    """
    urls = extract_candidate_offer_urls(html)
    assert urls == [
        "https://allegro.pl/oferta/abc-123",
        "https://allegro.pl/produkt/something?param=1&offerId=XYZ789",
    ]


def test_extract_price_prefers_json_ld(tmp_path):
    html = """
    <script type="application/ld+json">
    {"@type":"Product","offers":{"price":"149.50","priceCurrency":"PLN"}}
    </script>
    """
    price = extract_price(html)
    assert price == Decimal("149.50")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Sprzedano 123 sztuki", 123),
        ("Kupiono 45 razy", 45),
        ("123 osób kupiło", 123),
        ("Liczba sprzedanych ofert: 7", 7),
        ("brak danych", None),
    ],
)
def test_extract_sold_count_variants(text, expected):
    assert extract_sold_count(text) == expected


@pytest.mark.asyncio
async def test_choose_lowest_offer_picks_min_price(monkeypatch):
    listing_html = """
    <a href="https://allegro.pl/oferta/offer-1">1</a>
    <a href="https://allegro.pl/oferta/offer-2">2</a>
    <a href="/produkt/item?offerId=offer-3">3</a>
    """

    offer_pages = {
        "https://allegro.pl/oferta/offer-1": '<meta property="product:price:amount" content="199.99">sprzedano 5',
        "https://allegro.pl/oferta/offer-2": '<script type="application/ld+json">{"offers":{"price":"149.50"}}</script>sprzedano 12',
        "https://allegro.pl/produkt/item?offerId=offer-3": '<meta itemprop="price" content="155.00">kupiono 3',
    }

    class DummyClient:
        async def fetch_html(self, url, session_id=None):
            if "listing" in url:
                return FetchResult(html=listing_html, status_code=200, blocked=False, error=None, meta={"variant": "v1"})
            body = offer_pages.get(url)
            return FetchResult(html=body, status_code=200, blocked=False, error=None, meta={"url": url})

    result = await choose_lowest_offer("5901234567890", client=DummyClient(), max_candidates=8, timeout_seconds=30)

    assert result.price == Decimal("149.50")
    assert result.sold_count == 12
    assert result.is_not_found is False
    assert result.blocked is False
    assert result.raw_payload.get("offer_url") == "https://allegro.pl/oferta/offer-2"


@pytest.mark.asyncio
async def test_choose_lowest_offer_blocked_listing(monkeypatch):
    class DummyClient:
        async def fetch_html(self, url, session_id=None):
            return FetchResult(html=None, status_code=403, blocked=True, error="http_403", meta={"status_code": 403})

    result = await choose_lowest_offer("5900000000000", client=DummyClient(), timeout_seconds=5)

    assert result.is_temporary_error is True
    assert result.blocked is True
    assert result.price is None
    assert result.raw_payload.get("stage") == "listing"
