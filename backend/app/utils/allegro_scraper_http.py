from __future__ import annotations

import asyncio
import json
import logging
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
from app.utils.fingerprint import get_http_header_preset

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
logger = logging.getLogger(__name__)


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


def _request_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("CLOUD_SCRAPER_REQUEST_TIMEOUT", "25")))
    except Exception:
        return 25.0


def _http_timeout() -> httpx.Timeout:
    total = _request_timeout_seconds()
    connect = min(total, max(3.0, float(os.getenv("CLOUD_SCRAPER_CONNECT_TIMEOUT", "10"))))
    read_timeout = min(total, max(3.0, float(os.getenv("CLOUD_SCRAPER_READ_TIMEOUT", "20"))))
    return httpx.Timeout(total, connect=connect, read=read_timeout, write=read_timeout, pool=connect)


def _http_max_attempts() -> int:
    try:
        return max(1, int(os.getenv("CLOUD_SCRAPER_MAX_RETRIES", "3")))
    except Exception:
        return 3


def _http_backoff(attempt: int) -> float:
    try:
        base = float(os.getenv("CLOUD_SCRAPER_RETRY_BACKOFF", "1.5"))
    except Exception:
        base = 1.5
    return min(10.0, base * max(1, attempt - 1))


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


def _clear_blocked() -> None:
    global _BLOCKED_UNTIL
    with _COOLDOWN_LOCK:
        _BLOCKED_UNTIL = 0.0


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
    if not settings.proxy_list:
        _clear_blocked()
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
    request_started = time.monotonic()

    proxy = None
    if settings.proxy_list:
        proxy = random.choice(settings.proxy_list)

    fingerprint_id = None
    try:
        header_preset = get_http_header_preset()
        fingerprint_id = header_preset.fingerprint_id if header_preset else None
        client_kwargs = {"timeout": _http_timeout()}
        if proxy:
            client_kwargs["proxy"] = proxy
        if header_preset:
            client_kwargs["headers"] = header_preset.headers
            logger.info(
                "cloud_http fingerprint preset_id=%s ua_hash=%s ua_version=%s rotated=%s fingerprint_id=%s proxy=%s",
                header_preset.preset_id,
                header_preset.ua_hash,
                header_preset.ua_version,
                header_preset.rotated,
                fingerprint_id,
                proxy,
            )
        attempts = _http_max_attempts()
        async with httpx.AsyncClient(**client_kwargs) as client:
            for attempt in range(1, attempts + 1):
                attempt_started = time.monotonic()
                try:
                    resp = await client.get(url, params=params, timeout=_request_timeout_seconds())
                    request_started = request_started or attempt_started
                    break
                except httpx.ConnectError as exc:
                    duration = time.monotonic() - attempt_started
                    if attempt >= attempts:
                        raise
                    logger.warning(
                        "cloud_http retry ean=%s attempt=%s/%s stage=connect duration=%.2fs err=%r proxy=%s fingerprint_id=%s",
                        ean,
                        attempt,
                        attempts,
                        duration,
                        exc,
                        proxy,
                        fingerprint_id,
                    )
                    await asyncio.sleep(_http_backoff(attempt))
                except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                    duration = time.monotonic() - attempt_started
                    if attempt >= attempts:
                        raise
                    logger.warning(
                        "cloud_http retry ean=%s attempt=%s/%s stage=request_timeout duration=%.2fs err=%r proxy=%s fingerprint_id=%s",
                        ean,
                        attempt,
                        attempts,
                        duration,
                        exc,
                        proxy,
                        fingerprint_id,
                    )
                    await asyncio.sleep(_http_backoff(attempt))
    except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
        _reset_captcha_streak()
        duration = time.monotonic() - request_started if request_started else None
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": "timeout",
                "error_type": type(exc).__name__,
                "error_detail": repr(exc),
                "source": "cloud_http",
                "fingerprint_id": fingerprint_id,
                "proxy": proxy,
                "duration_seconds": round(duration, 2) if duration else None,
            },
            error="timeout",
            source="cloud_http",
            last_checked_at=now,
            fingerprint_id=fingerprint_id,
        )
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
                "fingerprint_id": fingerprint_id,
                "proxy": proxy,
            },
            error="network_error",
            source="cloud_http",
            last_checked_at=now,
            fingerprint_id=fingerprint_id,
        )
    except Exception as exc:
        _reset_captcha_streak()
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={
                "error": str(exc),
                "source": "cloud_http",
                "fingerprint_id": fingerprint_id,
                "proxy": proxy,
            },
            source="cloud_http",
            last_checked_at=now,
            fingerprint_id=fingerprint_id,
        )

    payload: Dict[str, object] = {
        "status_code": resp.status_code,
        "fingerprint_id": fingerprint_id,
        "proxy": proxy,
    }
    try:
        payload["body_snippet"] = resp.text[:500]
    except Exception:
        pass

    payload["request_duration_seconds"] = round(time.monotonic() - request_started, 2) if request_started else None

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
            fingerprint_id=fingerprint_id,
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
            fingerprint_id=fingerprint_id,
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
                fingerprint_id=fingerprint_id,
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
                    fingerprint_id=fingerprint_id,
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
                fingerprint_id=fingerprint_id,
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
        fingerprint_id=fingerprint_id,
    )
