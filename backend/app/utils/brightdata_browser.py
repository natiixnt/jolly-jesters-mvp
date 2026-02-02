"""Bright Data Browser API scraper (remote browser via Super Proxy).

Primary provider for Allegro scraping. Keeps the output contract identical to legacy scraper
by returning AllegroResult. Network/session details are configurable via env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Tuple

import redis
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

from app.core.config import settings
from app.services.schemas import AllegroResult
from app.utils.bd_unlocker_client import _parse_listing_offers, _parse_pdp_sold_count
import selenium
from app.utils.brightdata_metrics import read_snapshot, record_outcome
from app.utils.rate_limiter import RateLimited, rate_limiter, host_from_url

logger = logging.getLogger(__name__)

# --- simple in-process counters for quick observability ---
_COUNTERS = {
    "total": 0,
    "success": 0,
    "blocked_captcha": 0,
    "blocked_429": 0,
    "timeout_nav": 0,
    "timeout_selector": 0,
    "other_webdriver_error": 0,
    "error": 0,
}
_COUNTERS_LOCK = threading.Lock()


def _inc(metric: str):
    with _COUNTERS_LOCK:
        _COUNTERS[metric] = _COUNTERS.get(metric, 0) + 1
        _COUNTERS["total"] = _COUNTERS.get("total", 0) + 1
        total = _COUNTERS["total"]
        if total % 100 == 0:
            success = _COUNTERS.get("success", 0)
            blocked = _COUNTERS.get("blocked_captcha", 0) + _COUNTERS.get("blocked_429", 0)
            timeouts = _COUNTERS.get("timeout_nav", 0) + _COUNTERS.get("timeout_selector", 0)
            other = _COUNTERS.get("other_webdriver_error", 0) + _COUNTERS.get("error", 0)
            success_rate = (success / total * 100) if total else 0
            logger.info(
                "BRD_METRICS total=%s success=%s blocked=%s timeouts=%s other_err=%s success_rate=%.1f%%",
                total,
                success,
                blocked,
                timeouts,
                other,
                success_rate,
            )

# --- block reason tagging ---
_SIGS = {
    "cf_turnstile": [
        "challenges.cloudflare.com",
        "cf-challenge",
        "turnstile",
        "__cf_chl",
        "cloudflare",
    ],
    "datadome": [
        "datadome",
        "dd_captcha",
        "geo.datadome",
    ],
    "perimeterx": [
        "perimeterx",
        "px-captcha",
        "px-block",
        "_px3",
        "_pxhd",
        "_pxvid",
    ],
    "recaptcha": [
        "google.com/recaptcha",
        "g-recaptcha",
        "recaptcha",
    ],
    "hcaptcha": [
        "hcaptcha.com",
        "h-captcha",
        "hcaptcha",
    ],
    "akamai": [
        "akamai",
        "bot manager",
        "reference #",
    ],
    "blocked_429": [
        "429",
        "too many requests",
        "rate limit",
    ],
    "access_denied": [
        "access denied",
        "request blocked",
        "forbidden",
        "403",
    ],
}


def classify_block_reason(html: str, status_code: Optional[int]) -> str:
    lowered = (html or "").lower()
    if status_code == 429:
        return "blocked_429"
    for tag, patterns in _SIGS.items():
        for p in patterns:
            if p in lowered:
                return tag
    return "unknown"

# --- block reason tagging ---
_SIGS = {
    "cf_turnstile": [
        "challenges.cloudflare.com",
        "cf-challenge",
        "turnstile",
        "__cf_chl",
        "cloudflare",
    ],
    "datadome": [
        "datadome",
        "dd_captcha",
        "geo.datadome",
    ],
    "perimeterx": [
        "perimeterx",
        "px-captcha",
        "px-block",
        "_px3",
        "_pxhd",
        "_pxvid",
    ],
    "recaptcha": [
        "google.com/recaptcha",
        "g-recaptcha",
        "recaptcha",
    ],
    "hcaptcha": [
        "hcaptcha.com",
        "h-captcha",
        "hcaptcha",
    ],
    "akamai": [
        "akamai",
        "bot manager",
        "reference #",
    ],
    "blocked_429": [
        "429",
        "too many requests",
        "rate limit",
    ],
    "access_denied": [
        "access denied",
        "request blocked",
        "forbidden",
        "403",
    ],
}


def classify_block_reason(html: str, status_code: Optional[int]) -> str:
    lowered = (html or "").lower()
    if status_code == 429:
        return "blocked_429"
    for tag, patterns in _SIGS.items():
        for p in patterns:
            if p in lowered:
                return tag
    return "unknown"

# ---------- Env helpers ----------


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def _scraper_concurrency() -> int:
    try:
        return max(1, int(os.getenv("SCRAPER_CONCURRENCY", "1")))
    except Exception:
        return 1


def _pool_size() -> int:
    try:
        return max(1, int(os.getenv("SBR_POOL_SIZE", "2")))
    except Exception:
        return 2


def _max_req_per_session() -> int:
    try:
        return max(1, int(os.getenv("SBR_MAX_REQ_PER_SESSION", "20")))
    except Exception:
        return 20


def _max_session_minutes() -> int:
    try:
        return max(1, int(os.getenv("SBR_MAX_SESSION_MINUTES", "15")))
    except Exception:
        return 15


def _cooldown_minutes() -> int:
    try:
        return max(1, int(os.getenv("SBR_COOLDOWN_MINUTES", "60")))
    except Exception:
        return 60


def _max_retries() -> int:
    try:
        return max(1, int(os.getenv("SBR_MAX_RETRIES", "3")))
    except Exception:
        return 3


def _dump_dir() -> str:
    return os.getenv("SBR_DUMP_DIR", "/workspace/brd_dumps")


def _ean_cache_ttl_days() -> int:
    try:
        return max(1, int(os.getenv("EAN_CACHE_TTL_DAYS", "14")))
    except Exception:
        return 14


def _tie_break_limit() -> int:
    try:
        return max(1, int(os.getenv("SBR_TIE_BREAK_LIMIT", "3")))
    except Exception:
        return 3


# ---------- Cache ----------

_CACHE = None


def _redis_client():
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        except Exception:
            _CACHE = None
    return _CACHE


def _cache_key(ean: str) -> str:
    return f"sbr:ean:{ean}"


def _cache_get(ean: str) -> Optional[AllegroResult]:
    client = _redis_client()
    if not client:
        return None
    try:
        data = client.hgetall(_cache_key(ean))
        if not data:
            return None
        if data.get("expires_at") and float(data["expires_at"]) < time.time():
            client.delete(_cache_key(ean))
            return None
        price = Decimal(data["price"]) if data.get("price") else None
        sold_count = int(data["sold_count"]) if data.get("sold_count") else None
        payload = {"provider": "brightdata", "cached": True}
        return AllegroResult(
            price=price,
            sold_count=sold_count,
            is_not_found=data.get("is_not_found") == "1",
            is_temporary_error=False,
            raw_payload=payload,
            source="brightdata",
            last_checked_at=datetime.fromtimestamp(float(data.get("last_checked_at", time.time())), tz=timezone.utc),
        )
    except Exception:
        return None


def _ensure_dump_dir() -> str:
    path = _dump_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _dump_artifacts(driver: webdriver.Remote, ean: str, label: str):
    """Save HTML + screenshot for diagnostics; best-effort, never raising."""
    base = f"{ean}_{label}_{int(time.time())}"
    folder = _ensure_dump_dir()
    html_path = os.path.join(folder, f"{base}.html")
    png_path = os.path.join(folder, f"{base}.png")
    current_url = None
    try:
        current_url = driver.current_url
    except Exception:
        current_url = "unknown"
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source or "")
    except Exception:
        html_path = "write_failed"
    try:
        driver.save_screenshot(png_path)
    except Exception:
        png_path = "screenshot_failed"
    logger.warning("BRD_DUMP ean=%s label=%s url=%s html=%s png=%s", ean, label, current_url, html_path, png_path)


def _cache_set(ean: str, result: AllegroResult):
    client = _redis_client()
    if not client:
        return
    ttl_days = _ean_cache_ttl_days()
    expires_at = time.time() + ttl_days * 86400
    try:
        client.hset(
            _cache_key(ean),
            mapping={
                "price": str(result.price) if result.price is not None else "",
                "sold_count": str(result.sold_count) if result.sold_count is not None else "",
                "is_not_found": "1" if result.is_not_found else "0",
                "last_checked_at": str(time.time()),
                "expires_at": str(expires_at),
            },
        )
        client.expire(_cache_key(ean), int(ttl_days * 86400))
    except Exception:
        return


# ---------- Session management ----------


@dataclass
class SessionWrapper:
    driver: webdriver.Remote
    created_at: float
    last_used: float
    requests: int
    cooldown_until: float = 0.0

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) / 60 > _max_session_minutes()

    @property
    def hit_limit(self) -> bool:
        return self.requests >= _max_req_per_session()

    @property
    def cooling_down(self) -> bool:
        return time.time() < self.cooldown_until

    def close(self):
        """Hard-close the underlying driver."""
        try:
            self.driver.quit()
        except Exception:
            pass
        self.cooldown_until = time.time() + _cooldown_minutes() * 60

    def mark_used(self):
        self.requests += 1
        self.last_used = time.time()

    def mark_bad(self):
        self.cooldown_until = time.time() + _cooldown_minutes() * 60


class SessionPool:
    def __init__(self):
        self.pool: list[SessionWrapper] = []
        self.lock = asyncio.Lock()

    def _safe_user(self, username: str | None) -> str:
        if not username:
            return "missing"
        if len(username) <= 4:
            return "****"
        return f"***{username[-4:]}"

    def _log_driver_failure(self, endpoint: str, username: str | None, exc: Exception):
        logger.exception(
            "BRD_DRIVER_INIT_FAILED endpoint=%s user=%s selenium=%s",
            endpoint,
            self._safe_user(username),
            selenium.__version__,
            exc_info=exc,
        )

    def _log_driver_success(self, endpoint: str, username: str | None, driver: webdriver.Remote):
        try:
            caps = driver.capabilities or {}
        except Exception:
            caps = {}
        logger.info(
            "BRD_DRIVER_READY endpoint=%s user=%s browser=%s version=%s driver=%s headless=%s",
            endpoint,
            self._safe_user(username),
            caps.get("browserName"),
            caps.get("browserVersion") or caps.get("version"),
            (caps.get("chrome") or {}).get("chromedriverVersion"),
            True,
        )

    def _drop(self, session: SessionWrapper):
        try:
            session.close()
        finally:
            try:
                self.pool.remove(session)
            except ValueError:
                pass

    def _build_webdriver(self) -> Optional[webdriver.Remote]:
        username = _env("BRD_SBR_USERNAME")
        password = _env("BRD_SBR_PASSWORD")
        host = _env("BRD_SBR_HOST", "brd.superproxy.io")
        port = _env("BRD_SBR_WEBDRIVER_PORT", "9515")
        if not username or not password:
            return None
        endpoint = f"https://{host}:{port}"
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1280,720")
        options.set_capability("acceptInsecureCerts", True)
        try:
            driver = webdriver.Remote(
                command_executor=f"https://{username}:{password}@{host}:{port}",
                options=options,
            )
            driver.set_page_load_timeout(int(os.getenv("SBR_NAV_TIMEOUT", "90")))
            driver.set_script_timeout(int(os.getenv("SBR_SCRIPT_TIMEOUT", "60")))
            self._log_driver_success(endpoint, username, driver)
            return driver
        except Exception as exc:
            self._log_driver_failure(endpoint, username, exc)
            return None

    async def acquire(self) -> Tuple[Optional[SessionWrapper], Optional[str]]:
        async with self.lock:
            # purge bad/expired
            alive = []
            for s in self.pool:
                if s.expired or s.hit_limit:
                    try:
                        s.driver.quit()
                    except Exception:
                        pass
                    continue
                alive.append(s)
            self.pool = alive

            for s in self.pool:
                if not s.cooling_down:
                    return s, None

            if len(self.pool) < _pool_size():
                driver = self._build_webdriver()
                if driver:
                    wrapper = SessionWrapper(driver=driver, created_at=time.time(), last_used=time.time(), requests=0)
                    self.pool.append(wrapper)
                    return wrapper, None
                return None, "driver_init_failed"

            # All cooling down
            return None, "all_cooling_down"


SESSION_POOL = SessionPool()
CONCURRENCY_SEM = asyncio.Semaphore(_scraper_concurrency())


# ---------- Helpers ----------


def _blocked(html: str) -> bool:
    blocked, _, _ = _blocked_info(html)
    return blocked


def _blocked_info(html: str) -> Tuple[bool, Optional[int], Optional[str]]:
    lowered = html.lower()
    if "429" in lowered or "too many requests" in lowered:
        return True, 429, "rate_limited"
    if "captcha" in lowered or "robot" in lowered:
        return True, None, "captcha"
    if "access denied" in lowered or "forbidden" in lowered or "403" in lowered:
        return True, 403, "forbidden"
    if "przepraszamy" in lowered:
        return True, None, "soft_block"
    return False, None, None


async def _sleep_jitter():
    await asyncio.sleep(random.uniform(10, 30))


# ---------- Main fetch ----------


async def fetch_via_brightdata(ean: str) -> AllegroResult:
    started = time.monotonic()
    cached = _cache_get(ean)
    if cached:
        record_outcome("success" if not cached.is_not_found else "no_results", time.monotonic() - started, cached=True)
        return cached

    async with CONCURRENCY_SEM:
        backoffs = [2, 5, 10]
        last_result: Optional[AllegroResult] = None

        for attempt in range(1, _max_retries() + 1):
            session, error = await SESSION_POOL.acquire()
            if not session:
                _inc("error")
                last_result = AllegroResult(
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={"error": error or "no_session", "provider": "brightdata"},
                    error=error or "no_session",
                    source="brightdata",
                    last_checked_at=datetime.now(timezone.utc),
                )
                record_outcome("error", time.monotonic() - started, blocked=False)
                break

            retryable = False

            try:
                listing_url = f"https://allegro.pl/listing?string={ean}&order=qd&offerTypeBuyNow=1"
                host = host_from_url(listing_url)
                async with rate_limiter.throttle(listing_url):
                    session.driver.get(listing_url)
                html = session.driver.page_source or ""
                session.mark_used()
                is_blocked, status_code, block_reason = _blocked_info(html)
                if is_blocked:
                    tagged_reason = classify_block_reason(html, status_code) or (block_reason or "unknown")
                    _dump_artifacts(session.driver, ean, block_reason or "blocked")
                    session.mark_bad()
                    SESSION_POOL._drop(session)
                    retryable = True
                    if status_code == 429:
                        _inc("blocked_429")
                        cooldown_min = rate_limiter.register_429(host)
                    else:
                        _inc("blocked_captcha")
                        cooldown_min = None
                    last_result = AllegroResult(
                        price=None,
                        sold_count=None,
                        is_not_found=False,
                        is_temporary_error=True,
                        raw_payload={
                            "error": "blocked",
                            "provider": "brightdata",
                            "block_reason": tagged_reason,
                            "status_code": status_code,
                            "cooldown_minutes": cooldown_min,
                        },
                        source="brightdata",
                        last_checked_at=datetime.now(timezone.utc),
                        blocked=True,
                    )
                    record_outcome("blocked", time.monotonic() - started, blocked=True, status_code=status_code)
                    if status_code == 429:
                        break  # no fast retry; cooldown already applied
                    if attempt < _max_retries():
                        await asyncio.sleep(
                            random.uniform(
                                backoffs[min(attempt - 1, len(backoffs) - 1)],
                                backoffs[min(attempt - 1, len(backoffs) - 1)] + 1,
                            )
                        )
                        continue
                    break

                offers, auctions_only = _parse_listing_offers(html, page=1)
                if not offers:
                    _inc("success")
                    status = "auctions_only" if auctions_only else "no_results"
                    last_result = AllegroResult(
                        price=None,
                        sold_count=None,
                        is_not_found=True,
                        is_temporary_error=False,
                        raw_payload={"provider": "brightdata", "sold_count_status": status},
                        source="brightdata",
                        last_checked_at=datetime.now(timezone.utc),
                    )
                    _cache_set(ean, last_result)
                    rate_limiter.reset_429(host)
                    record_outcome("no_results", time.monotonic() - started)
                    break

                priced = [o for o in offers if o.price is not None]
                min_price = min(o.price for o in priced)
                candidates = [o for o in priced if o.price == min_price][: _tie_break_limit()]
                await _sleep_jitter()
                for cand in candidates:
                    try:
                        if not cand.url:
                            continue
                        async with rate_limiter.throttle(cand.url):
                            session.driver.get(cand.url)
                        page_html = session.driver.page_source or ""
                        session.mark_used()
                        cand.sold_count = _parse_pdp_sold_count(page_html)
                        cand.sold_count_status = "ok" if cand.sold_count is not None else "not_visible"
                        await _sleep_jitter()
                    except TimeoutException:
                        _dump_artifacts(session.driver, ean, "pdp_timeout")
                        cand.sold_count = None
                        cand.sold_count_status = "timeout"
                        session.mark_bad()
                        SESSION_POOL._drop(session)
                        retryable = True
                        break
                    except WebDriverException:
                        cand.sold_count = None
                        cand.sold_count_status = "error"

                if retryable:
                    if attempt < _max_retries():
                        await asyncio.sleep(
                            random.uniform(
                                backoffs[min(attempt - 1, len(backoffs) - 1)],
                                backoffs[min(attempt - 1, len(backoffs) - 1)] + 1,
                            )
                        )
                        continue
                    last_result = AllegroResult(
                        price=None,
                        sold_count=None,
                        is_not_found=False,
                        is_temporary_error=True,
                        raw_payload={"error": "pdp_retry_exhausted", "provider": "brightdata"},
                        source="brightdata",
                        last_checked_at=datetime.now(timezone.utc),
                        error="brightdata_error",
                    )
                    record_outcome("error", time.monotonic() - started)
                    break

                chosen = sorted(
                    candidates,
                    key=lambda o: (-(o.sold_count if o.sold_count is not None else -1), o.offer_id or ""),
                )[0]

                payload = {
                    "provider": "brightdata",
                    "offers": [
                        {
                            "offer_id": o.offer_id,
                            "url": o.url,
                            "price": o.price,
                            "currency": o.currency,
                            "sold_count": getattr(o, "sold_count", None),
                            "sold_count_status": getattr(o, "sold_count_status", None),
                            "page": o.page,
                        }
                        for o in offers
                    ],
                    "lowest_price_offer_id": chosen.offer_id,
                    "lowest_price_offer_url": chosen.url,
                    "sold_count_status": getattr(chosen, "sold_count_status", None),
                }

                last_result = AllegroResult(
                    price=Decimal(str(chosen.price)) if chosen.price is not None else None,
                    sold_count=getattr(chosen, "sold_count", None),
                    is_not_found=False,
                    is_temporary_error=False,
                    raw_payload=payload,
                    source="brightdata",
                    last_checked_at=datetime.now(timezone.utc),
                )
                _cache_set(ean, last_result)
                _inc("success")
                rate_limiter.reset_429(host)
                record_outcome("success", time.monotonic() - started)
                break
            except TimeoutException:
                _dump_artifacts(session.driver, ean, "timeout")
                session.mark_bad()
                SESSION_POOL._drop(session)
                retryable = True
                _inc("timeout_nav")
                last_result = AllegroResult(
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={"error": "timeout", "provider": "brightdata"},
                    source="brightdata",
                    last_checked_at=datetime.now(timezone.utc),
                    error="brightdata_error",
                )
                record_outcome("error", time.monotonic() - started)
            except RateLimited as rl:
                # honor cooldown; no fast retry
                _inc("blocked_429")
                cooldown_min = rl.remaining_seconds / 60
                last_result = AllegroResult(
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={
                        "error": "blocked",
                        "provider": "brightdata",
                        "block_reason": "blocked_429_cooldown",
                        "cooldown_minutes": cooldown_min,
                    },
                    source="brightdata",
                    last_checked_at=datetime.now(timezone.utc),
                    blocked=True,
                )
                record_outcome("blocked", time.monotonic() - started, blocked=True, status_code=429)
                break
            except WebDriverException as exc:
                _dump_artifacts(session.driver, ean, "webdriver_error")
                session.mark_bad()
                SESSION_POOL._drop(session)
                retryable = True
                _inc("other_webdriver_error")
                last_result = AllegroResult(
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={"error": repr(exc), "provider": "brightdata"},
                    source="brightdata",
                    last_checked_at=datetime.now(timezone.utc),
                    error="brightdata_error",
                )
                record_outcome("error", time.monotonic() - started)
            except Exception as exc:
                session.mark_bad()
                _inc("error")
                last_result = AllegroResult(
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={"error": repr(exc), "provider": "brightdata"},
                    source="brightdata",
                    last_checked_at=datetime.now(timezone.utc),
                    error="brightdata_error",
                )
                record_outcome("error", time.monotonic() - started)

            if retryable and attempt < _max_retries():
                await asyncio.sleep(
                    random.uniform(
                        backoffs[min(attempt - 1, len(backoffs) - 1)],
                        backoffs[min(attempt - 1, len(backoffs) - 1)] + 1,
                    )
                )
                continue
            else:
                break

        return last_result or AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "unknown", "provider": "brightdata"},
            source="brightdata",
            last_checked_at=datetime.now(timezone.utc),
            error="brightdata_error",
        )


def brightdata_status() -> dict:
    """Expose minimal stats for UI/health endpoints (no secrets)."""
    return {
        "mode": (os.getenv("SCRAPER_MODE") or "brightdata").strip().lower(),
        "metrics": read_snapshot(days=2),
    }
