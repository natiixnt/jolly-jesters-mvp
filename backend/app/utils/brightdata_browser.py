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
from app.utils.bd_unlocker_client import (
    fetch_listing_html_via_unlocker,
    parse_offers_from_listing_html,
    fetch_offer_html_via_unlocker,
    parse_sold_count_from_offer_html,
    OfferCandidate,
    SoldCountResult,
)
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
    "blocked_429_listing": 0,
    "blocked_429_pdp": 0,
    "timeout_nav": 0,
    "timeout_selector": 0,
    "other_webdriver_error": 0,
    "error": 0,
    "fallback_browser_used": 0,
    "sold_count_found_unlocker": 0,
    "sold_count_found_browser": 0,
    "sold_count_missing": 0,
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


def _apply_cdp_block(driver: webdriver.Remote):
    """Best-effort: block heavy resources (images/media/fonts/css) to reduce requests in fallback."""
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setBlockedURLs",
            {"urls": ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.woff", "*.woff2", "*.ttf", "*.otf", "*.mp4", "*.webm", "*.css"]},
        )
    except Exception:
        # remote drivers may not support CDP; ignore
        pass


async def _browser_fetch_sold(url: str) -> SoldCountResult:
    if not url:
        return SoldCountResult(sold_count=None, status="missing", reason="no_url")
    session, error = await SESSION_POOL.acquire()
    if not session:
        return SoldCountResult(sold_count=None, status="error", reason=error or "no_session")
    try:
        _apply_cdp_block(session.driver)
        async with rate_limiter.throttle(url):
            session.driver.get(url)
        html = session.driver.page_source or ""
        session.mark_used()
        sc = _parse_pdp_sold_count(html)
        if sc is not None:
            return SoldCountResult(sold_count=sc, status="visible", reason=None)
        return SoldCountResult(sold_count=None, status="missing", reason="browser_missing")
    except TimeoutException:
        _dump_artifacts(session.driver, url, "pdp_timeout_browser")
        session.mark_bad()
        SESSION_POOL._drop(session)
        _inc("timeout_nav")
        return SoldCountResult(sold_count=None, status="error", reason="timeout")
    except WebDriverException as exc:
        _dump_artifacts(session.driver, url, "webdriver_error_browser")
        session.mark_bad()
        SESSION_POOL._drop(session)
        _inc("other_webdriver_error")
        return SoldCountResult(sold_count=None, status="error", reason=repr(exc))
    except RateLimited:
        _inc("blocked_429")
    return SoldCountResult(sold_count=None, status="blocked", reason="429_cooldown")


def _is_allegro(url: str) -> bool:
    host = host_from_url(url)
    return host.endswith("allegro.pl") if host else False


async def _browser_flow_allegro(ean: str, listing_url: str, started: float) -> AllegroResult:
    session, error = await SESSION_POOL.acquire()
    if not session:
        _inc("error")
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": error or "no_session", "provider": "brightdata"},
            error=error or "no_session",
            source="brightdata",
            last_checked_at=datetime.now(timezone.utc),
        )
    host = host_from_url(listing_url)
    try:
        _apply_cdp_block(session.driver)
        async with rate_limiter.throttle(listing_url):
            session.driver.get(listing_url)
        html = session.driver.page_source or ""
        session.mark_used()
        is_blocked, status_code, block_reason = _blocked_info(html)
        if is_blocked:
            tagged_reason = classify_block_reason(html, status_code) or (block_reason or "unknown")
            _dump_artifacts(session.driver, ean, block_reason or "blocked_listing")
            _inc("blocked_429_listing" if status_code == 429 else "blocked_captcha")
            return AllegroResult(
                price=None,
                sold_count=None,
                is_not_found=False,
                is_temporary_error=True,
                raw_payload={"error": "blocked", "provider": "brightdata", "block_reason": tagged_reason, "status_code": status_code},
                source="brightdata",
                last_checked_at=datetime.now(timezone.utc),
                blocked=True,
            )

        offers = parse_offers_from_listing_html(html, limit=20)
        if not offers:
            result = AllegroResult(
                price=None,
                sold_count=None,
                is_not_found=True,
                is_temporary_error=False,
                raw_payload={"provider": "brightdata", "sold_count_status": "no_results"},
                source="brightdata",
                last_checked_at=datetime.now(timezone.utc),
            )
            _cache_set(ean, result)
            rate_limiter.reset_429(host)
            record_outcome("no_results", time.monotonic() - started)
            return result

        # only cheapest one to minimize navigations
        chosen_offer = ([o for o in offers if o.price is not None] or [offers[0]])[0]
        cand = chosen_offer
        if cand.url:
            try:
                async with rate_limiter.throttle(cand.url):
                    session.driver.get(cand.url)
                page_html = session.driver.page_source or ""
                session.mark_used()
                cand.sold_count = _parse_pdp_sold_count(page_html)
                cand.sold_count_status = "ok" if cand.sold_count is not None else "not_visible"
            except TimeoutException:
                _dump_artifacts(session.driver, ean, "pdp_timeout_browser")
                cand.sold_count = None
                cand.sold_count_status = "timeout"
                session.mark_bad()
                SESSION_POOL._drop(session)
            except WebDriverException:
                cand.sold_count = None
                cand.sold_count_status = "error"

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
                }
                for o in offers
            ],
            "lowest_price_offer_id": cand.offer_id,
            "lowest_price_offer_url": cand.url,
            "sold_count_status": getattr(cand, "sold_count_status", None),
        }

        result = AllegroResult(
            price=Decimal(str(cand.price)) if cand.price is not None else None,
            sold_count=getattr(cand, "sold_count", None),
            is_not_found=False,
            is_temporary_error=False,
            raw_payload=payload,
            source="brightdata",
            last_checked_at=datetime.now(timezone.utc),
        )
        _cache_set(ean, result)
        _inc("success")
        rate_limiter.reset_429(host)
        record_outcome("success", time.monotonic() - started)
        return result
    except RateLimited as rl:
        _inc("blocked_429_listing")
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "blocked", "provider": "brightdata", "block_reason": "blocked_429_cooldown", "cooldown_minutes": rl.remaining_seconds / 60},
            source="brightdata",
            last_checked_at=datetime.now(timezone.utc),
            blocked=True,
        )
    except Exception as exc:
        _inc("error")
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": repr(exc), "provider": "brightdata"},
            source="brightdata",
            last_checked_at=datetime.now(timezone.utc),
            error="brightdata_error",
        )
    finally:
        try:
            SESSION_POOL._drop(session)
        except Exception:
            pass


async def _unlocker_flow_generic(ean: str, listing_url: str, started: float) -> AllegroResult:
    try:
        listing_html = await fetch_listing_html_via_unlocker(ean)
    except RateLimited as rl:
        _inc("blocked_429")
        _inc("blocked_429_listing")
        minutes = rl.remaining_seconds / 60
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "blocked", "provider": "bd_unlocker", "block_reason": "blocked_429_cooldown", "cooldown_minutes": minutes},
            source="bd_unlocker",
            last_checked_at=datetime.now(timezone.utc),
            blocked=True,
        )
    if not listing_html:
        _inc("blocked_429_listing")
        return AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=False,
            is_temporary_error=True,
            raw_payload={"error": "blocked", "provider": "bd_unlocker", "block_reason": "listing_empty_or_blocked"},
            source="bd_unlocker",
            last_checked_at=datetime.now(timezone.utc),
            blocked=True,
        )

    offers = parse_offers_from_listing_html(listing_html, limit=20)
    if not offers:
        result = AllegroResult(
            price=None,
            sold_count=None,
            is_not_found=True,
            is_temporary_error=False,
            raw_payload={"provider": "bd_unlocker", "sold_count_status": "no_results"},
            source="bd_unlocker",
            last_checked_at=datetime.now(timezone.utc),
        )
        _cache_set(ean, result)
        record_outcome("no_results", time.monotonic() - started)
        return result

    cheapest = [o for o in offers if o.price is not None][:3] or offers[:3]
    chosen_offer = cheapest[0]
    sold_result: Optional[SoldCountResult] = None
    fallback_browser_used = False

    for cand in cheapest:
        html_pdp = await fetch_offer_html_via_unlocker(cand.url or "")
        sc = parse_sold_count_from_offer_html(html_pdp)
        if sc.status == "visible":
            sold_result = sc
            chosen_offer = cand
            _inc("sold_count_found_unlocker")
            _inc("success")
            break
        if sc.status == "blocked" and sc.reason == "429":
            _inc("blocked_429")
            _inc("blocked_429_pdp")
        elif sc.status == "blocked":
            _inc("blocked_captcha")
        else:
            _inc("sold_count_missing")

    if not sold_result or sold_result.sold_count is None:
        fallback_browser_used = True
        _inc("fallback_browser_used")
        sc = await _browser_fetch_sold(chosen_offer.url if chosen_offer else None)
        sold_result = sc
        if sc.status == "visible":
            _inc("sold_count_found_browser")
        elif sc.status == "blocked":
            if sc.reason and "429" in sc.reason:
                _inc("blocked_429")
                _inc("blocked_429_pdp")
            else:
                _inc("blocked_captcha")
        else:
            _inc("sold_count_missing")

    result = AllegroResult(
        price=chosen_offer.price if chosen_offer else None,
        sold_count=sold_result.sold_count if sold_result else None,
        is_not_found=False,
        is_temporary_error=False if sold_result and sold_result.status != "error" else True,
        raw_payload={
            "provider": "bd_unlocker" if not fallback_browser_used else "brightdata_browser_fallback",
            "offers_considered": [
                {"offer_id": o.offer_id, "url": o.url, "price": o.price, "currency": o.currency, "seller": o.seller}
                for o in cheapest
            ],
            "fallback_browser_used": fallback_browser_used,
            "blocked_reason": sold_result.reason if sold_result and sold_result.status == "blocked" else None,
            "sold_count_status": sold_result.status if sold_result else "missing",
        },
        source="bd_unlocker" if not fallback_browser_used else "brightdata_browser_fallback",
        last_checked_at=datetime.now(timezone.utc),
        product_url=chosen_offer.url if chosen_offer else None,
    )
    _cache_set(ean, result)
    record_outcome("success" if result.sold_count is not None else "no_results", time.monotonic() - started)
    return result

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

    listing_url = f"https://allegro.pl/listing?string={ean}&order=qd&offerTypeBuyNow=1"
    host = host_from_url(listing_url)
    if _is_allegro(listing_url):
        logger.info("FLOW_SELECT host=%s flow=browser_allegro", host)
        return await _browser_flow_allegro(ean, listing_url, started)
    else:
        logger.info("FLOW_SELECT host=%s flow=unlocker_generic", host)
        return await _unlocker_flow_generic(ean, listing_url, started)


def brightdata_status() -> dict:
    """Expose minimal stats for UI/health endpoints (no secrets)."""
    return {
        "mode": (os.getenv("SCRAPER_MODE") or "brightdata").strip().lower(),
        "metrics": read_snapshot(days=2),
    }
