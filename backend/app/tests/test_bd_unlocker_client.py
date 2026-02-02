import json
import os
import pytest
from decimal import Decimal

from app.utils import bd_unlocker_client as bd


def _listing_html(elements):
    state = {"__listing_StoreState": {"items": {"elements": elements}}}
    return f'<script data-serialize-box-id="1">{json.dumps(state)}</script>'


@pytest.mark.asyncio
async def test_bd_unlocker_tie_break_prefers_highest_sold(monkeypatch):
    os.environ["SCRAPER_MODE"] = "bd_unlocker"
    listing = _listing_html(
        [
            {
                "id": "offer1",
                "price": {"mainPrice": {"amount": "10.00", "currency": "PLN"}},
                "productPopularity": {"label": "Sprzedano 5"},
                "url": "/oferta/oferta1",
            },
            {
                "id": "offer2",
                "price": {"mainPrice": {"amount": "10.00", "currency": "PLN"}},
                "productPopularity": {"label": "Sprzedano 9"},
                "url": "/oferta/oferta2",
            },
        ]
    )
    pdp1 = "<div>sprzedano 5</div>"
    pdp2 = "<div>sprzedano 12</div>"

    async def fake_fetch(url, label):
        if label == "listing":
            return listing, {"cache": False}
        if "oferta1" in url:
            return pdp1, {"cache": False}
        return pdp2, {"cache": False}

    monkeypatch.setattr(bd, "_unlocker_fetch", fake_fetch)

    result = await bd.fetch_via_bd_unlocker("5901234567890")

    assert result.price == Decimal("10.00")
    assert result.sold_count == 12
    assert result.raw_payload["lowest_price_offer_id"] == "offer2"
    assert result.raw_payload["sold_count_status"] == "ok"
    assert result.is_not_found is False


@pytest.mark.asyncio
async def test_bd_unlocker_handles_auctions_only(monkeypatch):
    os.environ["SCRAPER_MODE"] = "bd_unlocker"
    listing = _listing_html(
        [
            {
                "id": "auction1",
                "isAuction": True,
                "price": {"mainPrice": {"amount": "20.00", "currency": "PLN"}},
            }
        ]
    )

    async def fake_fetch(url, label):
        return listing, {"cache": False}

    monkeypatch.setattr(bd, "_unlocker_fetch", fake_fetch)

    result = await bd.fetch_via_bd_unlocker("5900000000000")

    assert result.is_not_found is True
    assert result.raw_payload["sold_count_status"] == "auctions_only"
