"""Decodo Web Scraping API client.

Fetches rendered/HTML pages with retries/backoff and tolerant JSON response parsing.
Designed to be used as the primary fetch layer for Allegro scraping.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DECODO_ENDPOINT_DEFAULT = "https://scraper-api.decodo.com/v2/scrape"
_BLOCKED_MARKERS = ("captcha", "access denied", "cloudflare", "attention required")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _decodo_endpoint() -> str:
    return os.getenv("DECODO_ENDPOINT", DECODO_ENDPOINT_DEFAULT)


def _decodo_token() -> Optional[str]:
    return os.getenv("DECODO_TOKEN")


def _decodo_geo() -> str:
    return os.getenv("DECODO_GEO", "").strip()


def _decodo_locale() -> str:
    return os.getenv("DECODO_LOCALE", "").strip()


def _decodo_headless() -> str:
    return os.getenv("DECODO_HEADLESS", "html").strip()


def _decodo_device_type() -> str:
    return os.getenv("DECODO_DEVICE_TYPE", "desktop").strip() or "desktop"


def _decodo_xhr() -> bool:
    return _bool_env("DECODO_XHR", default=False)


def _decodo_timeout() -> httpx.Timeout:
    total = max(5.0, float(os.getenv("DECODO_TIMEOUT_SECONDS", os.getenv("DECODO_TIMEOUT", "150"))))
    connect = min(total, max(3.0, total / 3))
    read = min(total, max(5.0, total * 0.8))
    return httpx.Timeout(timeout=total, connect=connect, read=read, write=read, pool=connect)


def _decodo_max_attempts() -> int:
    try:
        return min(5, max(1, int(os.getenv("DECODO_MAX_RETRIES", "1"))))
    except Exception:
        return 1


def _decodo_backoff(attempt: int) -> float:
    try:
        base = float(os.getenv("DECODO_BACKOFF_BASE", "1.5"))
    except Exception:
        base = 1.5
    jitter = random.uniform(0.25, 0.85)
    return min(12.0, base * (2 ** max(0, attempt - 1)) + jitter)


def _request_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Basic {token}"
    return headers


def _payload_variants(url: str, session_id: Optional[str]) -> Iterable[Dict[str, Any]]:
    headless_env = _decodo_headless()
    geo_env = _decodo_geo()
    device = _decodo_device_type()
    locale = _decodo_locale()
    xhr_flag = _decodo_xhr()

    base = {
        "target": "universal",
        "url": url,
        "device_type": device,
    }
    if session_id:
        base["session_id"] = session_id
    if xhr_flag:
        base["xhr"] = True
    if locale:
        base["locale"] = locale

    combos = [
        {"headless": None, "geo": None},
        {"headless": None, "geo": geo_env or None},
        {"headless": headless_env or None, "geo": None},
        {"headless": headless_env or None, "geo": geo_env or None},
    ]

    for idx, combo in enumerate(combos, start=1):
        payload = dict(base)
        if combo["headless"]:
            payload["headless"] = combo["headless"]
        if combo["geo"]:
            payload["geo"] = combo["geo"]
        payload["variant"] = f"v{idx}"
        yield payload


def _extract_html_from_response(resp: httpx.Response) -> Tuple[Optional[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"status_code": resp.status_code}
    content_type = resp.headers.get("content-type", "")
    text = resp.text

    if "application/json" in content_type.lower():
        try:
            data = resp.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            meta["raw"] = list(data.keys())
            results = data.get("results") or data.get("result") or data.get("data")
            if isinstance(results, list) and results:
                first = results[0] or {}
                meta["task_id"] = first.get("task_id") or first.get("taskId")
                meta["request_id"] = first.get("request_id") or first.get("requestId")
                html = (
                    first.get("content")
                    or first.get("html")
                    or first.get("response")
                    or first.get("data", {}).get("content")
                )
                return html, meta
            if isinstance(results, dict):
                html = (
                    results.get("content")
                    or results.get("html")
                    or results.get("response")
                    or results.get("body")
                )
                return html, meta
            # Fallback: root-level fields
            html = data.get("content") or data.get("html") or data.get("body")
            if html:
                return html, meta
        return None, meta

    return text or None, meta


def _detect_blocked(html: Optional[str], status: int) -> Tuple[bool, Optional[str]]:
    if status in (403, 429):
        return True, f"status_{status}"
    lowered = (html or "").lower()
    for marker in _BLOCKED_MARKERS:
        if marker in lowered:
            return True, "html_marker"
    return False, None


@dataclass
class FetchResult:
    html: Optional[str]
    status_code: Optional[int]
    blocked: bool
    error: Optional[str]
    meta: Dict[str, Any]


class DecodoClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or _decodo_token()
        self.timeout = _decodo_timeout()
        self.max_attempts = _decodo_max_attempts()
        self.endpoint = _decodo_endpoint()

    async def fetch_html(self, url: str, session_id: Optional[str] = None) -> FetchResult:
        if not self.token:
            return FetchResult(
                html=None,
                status_code=None,
                blocked=False,
                error="decodo_token_missing",
                meta={"provider": "decodo"},
            )

        headers = _request_headers(self.token)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            last_meta: Dict[str, Any] = {}
            for payload in _payload_variants(url, session_id=session_id):
                variant = payload.get("variant")
                for attempt in range(1, self.max_attempts + 1):
                    started = time.monotonic()
                    try:
                        resp = await client.post(self.endpoint, headers=headers, json=payload)
                        duration = time.monotonic() - started
                        html, meta = _extract_html_from_response(resp)
                        meta.update(
                            {
                                "attempt": attempt,
                                "request_duration_seconds": round(duration, 2),
                                "provider": "decodo",
                                "endpoint": self.endpoint,
                                "variant": variant,
                                "status_code": resp.status_code,
                            }
                        )
                        last_meta = meta

                        blocked, block_reason = _detect_blocked(html, resp.status_code)
                        if blocked:
                            meta["blocked"] = True
                            meta["block_reason"] = block_reason
                        if resp.status_code >= 400:
                            meta["error"] = f"http_{resp.status_code}"
                            try:
                                meta["error_body"] = (resp.text or "")[:500]
                            except Exception:
                                pass
                            if blocked or attempt >= self.max_attempts:
                                return FetchResult(
                                    html=None,
                                    status_code=resp.status_code,
                                    blocked=blocked,
                                    error=meta.get("error"),
                                    meta=meta,
                                )
                            await asyncio.sleep(_decodo_backoff(attempt))
                            continue

                        if not html:
                            meta["error"] = "empty_body"
                            if attempt >= self.max_attempts:
                                return FetchResult(
                                    html=None,
                                    status_code=resp.status_code,
                                    blocked=blocked,
                                    error="empty_body",
                                    meta=meta,
                                )
                            await asyncio.sleep(_decodo_backoff(attempt))
                            continue

                        return FetchResult(
                            html=html,
                            status_code=resp.status_code,
                            blocked=blocked,
                            error=None,
                            meta=meta,
                        )
                    except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                        duration = time.monotonic() - started
                        meta = {
                            "error": "timeout",
                            "error_type": type(exc).__name__,
                            "provider": "decodo",
                            "attempt": attempt,
                            "variant": variant,
                            "request_duration_seconds": round(duration, 2),
                        }
                        last_meta = meta
                        if attempt >= self.max_attempts:
                            return FetchResult(
                                html=None,
                                status_code=None,
                                blocked=False,
                                error="timeout",
                                meta=meta,
                            )
                        await asyncio.sleep(_decodo_backoff(attempt))
                    except httpx.ConnectError as exc:
                        duration = time.monotonic() - started
                        meta = {
                            "error": "network_error",
                            "error_type": type(exc).__name__,
                            "provider": "decodo",
                            "attempt": attempt,
                            "variant": variant,
                            "request_duration_seconds": round(duration, 2),
                        }
                        last_meta = meta
                        if attempt >= self.max_attempts:
                            return FetchResult(
                                html=None,
                                status_code=None,
                                blocked=False,
                                error="network_error",
                                meta=meta,
                            )
                        await asyncio.sleep(_decodo_backoff(attempt))
                    except Exception as exc:  # pragma: no cover - defensive
                        meta = {
                            "error": str(exc),
                            "provider": "decodo",
                            "attempt": attempt,
                            "variant": variant,
                        }
                        return FetchResult(
                            html=None,
                            status_code=None,
                            blocked=False,
                            error=str(exc),
                            meta=meta,
                        )

        return FetchResult(html=None, status_code=None, blocked=False, error="unknown", meta=last_meta or {"provider": "decodo"})
