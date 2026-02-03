from decimal import Decimal
from pathlib import Path

import pytest

from app.utils.decodo_client import (
    fetch_via_decodo,
    parse_listing_lowest_offer,
    parse_offer_details,
)


@pytest.fixture()
def listing_html() -> str:
    path = Path(__file__).parent / "fixtures" / "decodo" / "listing.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture()
def offer_html() -> str:
    path = Path(__file__).parent / "fixtures" / "decodo" / "offer.html"
    return path.read_text(encoding="utf-8")


def test_parse_listing_lowest_offer_picks_min_price(listing_html):
    result = parse_listing_lowest_offer(listing_html)
    assert result.status == "ok"
    assert result.offer_url == "https://allegro.pl/oferta/xyz-test"
    assert result.price == Decimal("149.50")
    assert result.offers_count >= 2


def test_parse_offer_details_extracts_price_and_sold_count(offer_html):
    result = parse_offer_details(offer_html)
    assert result.price == Decimal("149.50")
    assert result.sold_count == 12
    assert result.sold_count_status == "ok"


@pytest.mark.asyncio
async def test_fetch_via_decodo_happy_path(monkeypatch, listing_html, offer_html):
    calls = []

    async def fake_request(url: str, session_id=None):
        calls.append(url)
        if "listing" in url:
            return listing_html, {"status_code": 200, "provider": "decodo"}
        return offer_html, {"status_code": 200, "provider": "decodo"}

    monkeypatch.setattr("app.utils.decodo_client._request_html", fake_request)

    result = await fetch_via_decodo("5901234567890")

    assert result.price == Decimal("149.50")
    assert result.sold_count == 12
    assert result.is_not_found is False
    assert result.is_temporary_error is False
    assert result.raw_payload.get("provider") == "decodo"
    assert result.raw_payload.get("sold_count_status") == "ok"
    assert len(calls) == 2
