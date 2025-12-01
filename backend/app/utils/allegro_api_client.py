from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import requests

from app.core.config import settings


@dataclass
class AllegroAPIStruct:
    price: Optional[Decimal]
    sold_count: Optional[int]
    raw_payload: dict


class AllegroAPIClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.allegro_api_token

    def fetch_by_ean(self, ean: str) -> Optional[AllegroAPIStruct]:
        """Placeholder Allegro API call. Returns None when API token is missing.

        The call is intentionally minimal for MVP; it can be extended with
        authenticated requests once real Allegro credentials are available.
        """

        if not self.token:
            return None

        try:
            resp = requests.get(
                "https://api.allegro.pl/public/allegro-offers",
                params={"ean": ean},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            if resp.status_code != 200:
                return AllegroAPIStruct(None, None, {"status": resp.status_code})
            payload = resp.json()
        except Exception:
            return AllegroAPIStruct(None, None, {"status": "error"})

        price = None
        sold_count = None
        try:
            offers = payload.get("offers") or []
            if offers:
                price_value = offers[0].get("price")
                price = Decimal(str(price_value)) if price_value is not None else None
                sold_count = offers[0].get("soldCount")
        except Exception:
            pass

        return AllegroAPIStruct(price=price, sold_count=sold_count, raw_payload=payload)
