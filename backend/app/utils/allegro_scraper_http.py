from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional

import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult

_NO_RESULTS_MARKERS = (
    "brak wynik\u00f3w",
    "nie znale\u017ali\u015bmy",
    "0 wynik\u00f3w",
    "no results",
    "no items found",
)

_BLOCKED_MARKERS = (
    "captcha",
    "captcha-delivery.com",
    "geo.captcha-delivery.com",
    "cloudflare",
    "attention required",
    "access denied",
)
_RATE_LOCK = threading.Lock()
_NEXT_ALLOWED_AT = 0.0
_COOLDOWN_LOCK = threading.Lock()
_BLOCKED_UNTIL = 0.0
_CAPTCHA_LOCK = threading.Lock()
_CAPTCHA_STREAK = 0


def _min_interval_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("CLOUD_SCRAPER_MIN_INTERVAL_SECONDS", "1.0")))
    except Exception:
        return 1.0


def _cooldown_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("CLOUD_SCRAPER_COOLDOWN_SECONDS", "60")))
    except Exception:
        return 60.0


def _wait_for_rate_limit() -> None:
    global _NEXT_ALLOWED_AT
    min_interval = _min_interval_seconds()
    if min_interval <= 0:
        return
    with _RATE_LOCK:
        now = time.monotonic()
        wait_for = _NEXT_ALLOWED_AT - now
        if wait_for > 0:
            time.sleep(wait_for)
            now = time.monotonic()
        _NEXT_ALLOWED_AT = now + min_interval


def _cooldown_remaining() -> float:
    now = time.monotonic()
    with _COOLDOWN_LOCK:
        remaining = _BLOCKED_UNTIL - now
    return max(0.0, remaining)


def _mark_blocked(cooldown_override: Optional[float] = None) -> None:
    global _BLOCKED_UNTIL
    cooldown = cooldown_override if cooldown_override is not None else _cooldown_seconds()
    if cooldown <= 0:
        return
    now = time.monotonic()
    with _COOLDOWN_LOCK:
        _BLOCKED_UNTIL = max(_BLOCKED_UNTIL, now + cooldown)


def _captcha_threshold() -> int:
    try:
        return max(1, int(os.getenv("CLOUD_SCRAPER_CAPTCHA_THRESHOLD", "3")))
    except Exception:
        return 3


def _captcha_cooldown_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("CLOUD_SCRAPER_CAPTCHA_COOLDOWN_SECONDS", "300")))
    except Exception:
        return 300.0


def _record_captcha() -> bool:
    global _CAPTCHA_STREAK
    threshold = _captcha_threshold()
    with _CAPTCHA_LOCK:
        _CAPTCHA_STREAK += 1
        if _CAPTCHA_STREAK >= threshold:
            _CAPTCHA_STREAK = 0
            return True
    return False


def _reset_captcha_streak() -> None:
    global _CAPTCHA_STREAK
    with _CAPTCHA_LOCK:
        _CAPTCHA_STREAK = 0


def _extract_listing_state(html: str) -> Optional[dict]:
    if "__listing_StoreState" not in html:
        return None
    for match in re.finditer(r"<script[^>]*data-serialize-box-id[^>]*>(.*?)</script>", html, re.DOTALL):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("__listing_StoreState"):
            return data
    return None


def _listing_elements_count(state: dict) -> Optional[int]:
    try:
        elements = state.get("__listing_StoreState", {}).get("items", {}).get("elements", [])
    except Exception:
        return None
    if not isinstance(elements, list):
        return None
    return len(elements)


def _html_has_no_results(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _NO_RESULTS_MARKERS)


def _detect_block_reason(html: str) -> Optional[str]:
    lowered = html.lower()
    if any(marker in lowered for marker in _BLOCKED_MARKERS):
        if "captcha" in lowered:
            return "captcha"
        if "cloudflare" in lowered or "attention required" in lowered:
            return "cloudflare"
        return "access_denied"
    if "<title>allegro.pl</title>" in lowered and "data-serialize-box-id" not in lowered:
        return "blocked_minimal_page"
    return None


async def fetch_via_http_scraper(ean: str) -> AllegroResult:
    now = datetime.now(timezone.utc)
    remaining = _cooldown_remaining()
    if remaining > 0:
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": "cooldown",
                "retry_after_seconds": round(remaining, 2),
                "source": "cloud_http",
            },
            source="cloud_http",
            last_checked_at=now,
            blocked=True,
        )
    _wait_for_rate_limit()
    url = "https://allegro.pl/listing"
    params = {"string": ean}

    proxy = None
    if settings.proxy_list:
        proxy = random.choice(settings.proxy_list)

    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    try:
        async with httpx.AsyncClient(timeout=settings.proxy_timeout, proxies=proxies) as client:
            resp = await client.get(url, params=params)
    except httpx.ConnectError as exc:
        _reset_captcha_streak()
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": "network_error",
                "error_type": type(exc).__name__,
                "error_detail": repr(exc),
                "source": "cloud_http",
            },
            error="network_error",
            source="cloud_http",
            last_checked_at=now,
        )
    except Exception as exc:
        _reset_captcha_streak()
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": str(exc), "source": "cloud_http"},
            source="cloud_http",
            last_checked_at=now,
        )

    payload: Dict[str, object] = {"status_code": resp.status_code}
    try:
        payload["body_snippet"] = resp.text[:500]
    except Exception:
        pass

    if resp.status_code == 404:
        _reset_captcha_streak()
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload=payload,
            source="cloud_http",
            last_checked_at=now,
        )

    if resp.status_code in (403, 429) or resp.status_code >= 500:
        _reset_captcha_streak()
        if resp.status_code in (403, 429):
            _mark_blocked()
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload=payload,
            source="cloud_http",
            last_checked_at=now,
            blocked=resp.status_code in (403, 429),
        )

    if resp.status_code == 200:
        html = resp.text or ""
        block_reason = _detect_block_reason(html)
        if block_reason:
            retry_after = None
            if block_reason == "captcha":
                triggered = _record_captcha()
                if triggered:
                    retry_after = _captcha_cooldown_seconds()
                    _mark_blocked(cooldown_override=retry_after)
                else:
                    _mark_blocked()
            else:
                _reset_captcha_streak()
                _mark_blocked()
            return AllegroResult(
                price=None,
                sold_count=None,
                is_not_found=False,
                is_temporary_error=True,
                raw_payload=payload
                | {
                    "note": "blocked",
                    "block_reason": block_reason,
                    "source": "cloud_http",
                    "retry_after_seconds": retry_after,
                },
                source="cloud_http",
                last_checked_at=now,
                blocked=True,
            )
        state = _extract_listing_state(html)
        if state:
            elements_count = _listing_elements_count(state)
            payload["listing_elements_count"] = elements_count
            if elements_count == 0:
                _reset_captcha_streak()
                return AllegroResult(
                    price=None,
                    sold_count=None,
                    is_not_found=True,
                    is_temporary_error=False,
                    raw_payload=payload | {"note": "no_results", "source": "cloud_http"},
                    source="cloud_http",
                    last_checked_at=now,
                )
        elif html and _html_has_no_results(html):
            _reset_captcha_streak()
            return AllegroResult(
                price=None,
                sold_count=None,
                is_not_found=True,
                is_temporary_error=False,
                raw_payload=payload | {"note": "no_results_text", "source": "cloud_http"},
                source="cloud_http",
                last_checked_at=now,
            )

    # No HTML parsing of offers - treat as temporary to allow local scraper fallback
    _reset_captcha_streak()
    return AllegroResult(
        price=None,
        sold_count=None,
        is_not_found=False,
        is_temporary_error=True,
        raw_payload=payload | {"note": "unparsed_html", "source": "cloud_http"},
        source="cloud_http",
        last_checked_at=now,
    )
