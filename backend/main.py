import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import random
from pathlib import Path
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from seleniumwire import webdriver
from selenium.common.exceptions import SessionNotCreatedException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.utils.fingerprint import (
    SeleniumFingerprint,
    force_rotate_profile,
    force_rotate_selenium_fingerprint,
    force_rotate_selenium_proxy,
    get_selenium_fingerprint,
    get_selenium_proxy,
    ua_hash,
    ua_version,
)

logger = logging.getLogger(__name__)
_LAST_DRIVER_DEBUG: Dict[str, Any] = {}
_FORCED_PROFILE_DIR: Optional[str] = None
_ALLEGRO_BASE_URL = "https://allegro.pl"
_FINGERPRINT_REQUESTS_SINCE_ROTATE = 0
_FINGERPRINT_ROTATE_AFTER = 0


def _env_flag_enabled(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_flag_disabled(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"0", "false", "no", "off"}


def _rotation_bounds_requests() -> Tuple[int, int]:
    try:
        min_val = int(os.getenv("SELENIUM_ROTATE_MIN_REQUESTS", "2"))
    except Exception:
        min_val = 2
    try:
        max_val = int(os.getenv("SELENIUM_ROTATE_MAX_REQUESTS", "3"))
    except Exception:
        max_val = 3
    min_val = max(1, min_val)
    max_val = max(min_val, max_val)
    return min_val, max_val


def _bump_rotation_counter(rotated: bool) -> Tuple[int, int]:
    global _FINGERPRINT_REQUESTS_SINCE_ROTATE, _FINGERPRINT_ROTATE_AFTER
    min_val, max_val = _rotation_bounds_requests()
    if rotated or _FINGERPRINT_ROTATE_AFTER <= 0:
        _FINGERPRINT_REQUESTS_SINCE_ROTATE = 0
        _FINGERPRINT_ROTATE_AFTER = random.randint(min_val, max_val)
    _FINGERPRINT_REQUESTS_SINCE_ROTATE += 1
    return _FINGERPRINT_REQUESTS_SINCE_ROTATE, _FINGERPRINT_ROTATE_AFTER


def _vnc_enabled() -> bool:
    return _env_flag_enabled(os.getenv("LOCAL_SCRAPER_ENABLE_VNC"))


def is_headless_mode() -> bool:
    if _env_flag_enabled(os.getenv("SELENIUM_HEADLESS")):
        return True
    if _env_flag_disabled(os.getenv("SELENIUM_HEADED")):
        return True
    return False


def get_scraper_mode() -> str:
    return "headless" if is_headless_mode() else "headed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_chrome_binary() -> Optional[str]:
    for env_key in ("SELENIUM_CHROME_BINARY", "CHROME_BIN", "CHROME_PATH"):
        value = os.getenv(env_key)
        if value:
            return value
    for candidate in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(candidate)
        if path:
            return path
    mac_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for path in mac_paths:
        if os.path.exists(path):
            return path
    return None


def _resolve_chromedriver_path() -> Optional[str]:
    for env_key in ("SELENIUM_CHROMEDRIVER", "CHROMEDRIVER_PATH"):
        value = os.getenv(env_key)
        if value:
            return value
    return shutil.which("chromedriver")


def _binary_version(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        output = subprocess.check_output([path, "--version"], text=True).strip()
        return output or None
    except Exception:
        return None


def _cleanup_profile_lock(user_data_dir: str) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = Path(user_data_dir) / name
        try:
            if path.exists():
                path.unlink()
        except Exception:
            continue


def _terminate_chrome_for_profile(user_data_dir: str) -> None:
    if not _env_flag_enabled(os.getenv("SELENIUM_KILL_EXISTING")):
        return
    target = f"--user-data-dir={user_data_dir}"
    pids: List[int] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_text(errors="ignore")
        except Exception:
            continue
        if target in cmdline and ("chromium" in cmdline or "chrome" in cmdline):
            try:
                pids.append(int(proc.name))
            except ValueError:
                continue
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            continue
    if pids:
        time.sleep(0.5)
        for pid in pids:
            try:
                os.kill(pid, 0)
            except Exception:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                continue


def _normalize_user_data_dir(value: str) -> str:
    return str(Path(value).expanduser())


def _new_temp_profile_dir() -> str:
    base = Path(os.getenv("SELENIUM_TEMP_PROFILE_DIR", "/tmp")) / "local-scraper-profile"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"profile-{time.time_ns()}")


def _get_forced_profile_dir() -> str:
    global _FORCED_PROFILE_DIR
    if not _FORCED_PROFILE_DIR:
        _FORCED_PROFILE_DIR = _new_temp_profile_dir()
    return _FORCED_PROFILE_DIR


def _build_chrome_options(
    user_data_dir_override: Optional[str] = None,
    headless_override: Optional[bool] = None,
    fingerprint: Optional[SeleniumFingerprint] = None,
) -> Options:
    headless = is_headless_mode() if headless_override is None else headless_override
    chrome_path = _resolve_chrome_binary()
    options = Options()
    if chrome_path:
        options.binary_location = chrome_path
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-software-rasterizer")
    if fingerprint:
        width, height = fingerprint.viewport
        options.add_argument(f"--window-size={width},{height}")
    else:
        options.add_argument("--window-size=1280,800")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    if fingerprint:
        options.add_argument(f"--lang={fingerprint.lang}")
    else:
        options.add_argument("--lang=pl-PL")
    options.add_argument("--ignore-certificate-errors")
    prefs: Dict[str, str] = {}
    if fingerprint:
        options.add_argument(f"--user-agent={fingerprint.user_agent}")
        if fingerprint.accept_language:
            prefs["intl.accept_languages"] = fingerprint.accept_language
    else:
        user_agent = os.getenv("SELENIUM_USER_AGENT")
        if user_agent:
            options.add_argument(f"--user-agent={user_agent}")
    if prefs:
        options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    user_data_dir = user_data_dir_override or os.getenv("SELENIUM_USER_DATA_DIR")
    if user_data_dir:
        user_data_dir = _normalize_user_data_dir(user_data_dir)
        _terminate_chrome_for_profile(user_data_dir)
        _cleanup_profile_lock(user_data_dir)
        options.add_argument(f"--user-data-dir={user_data_dir}")
    profile_dir = os.getenv("SELENIUM_PROFILE_DIR")
    if profile_dir:
        options.add_argument(f"--profile-directory={profile_dir}")
    chrome_log_path = os.getenv("SELENIUM_CHROME_LOG_PATH")
    if chrome_log_path:
        chrome_log_file = Path(chrome_log_path).expanduser()
        chrome_log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            chrome_log_file.touch(exist_ok=True)
        except Exception:
            pass
        options.add_argument("--enable-logging")
        options.add_argument("--v=1")
        options.add_argument(f"--log-path={chrome_log_file}")
    return options


def _listing_state_from_scripts(driver: WebDriver) -> Optional[Dict]:
    scripts = driver.find_elements(By.CSS_SELECTOR, "script[data-serialize-box-id]")
    for script in scripts:
        raw = script.get_attribute("innerHTML") or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("__listing_StoreState"):
            return data
    return None


def _listing_elements_count(state: Optional[Dict]) -> Optional[int]:
    if not state or not isinstance(state, dict):
        return None
    try:
        elements = state.get("__listing_StoreState", {}).get("items", {}).get("elements", [])
    except Exception:
        return None
    if not isinstance(elements, list):
        return None
    return len(elements)


def _detect_block_reason(driver: WebDriver, request_meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    status_code = None
    headers = {}
    if request_meta:
        try:
            status_code = int(request_meta.get("status_code")) if request_meta.get("status_code") is not None else None
        except Exception:
            status_code = None
        try:
            headers = request_meta.get("response_headers") or {}
        except Exception:
            headers = {}
    if status_code == 404:
        return None
    if status_code in (401, 403, 429):
        return f"http_{status_code}"
    if status_code and status_code >= 500:
        return f"http_{status_code}"
    lowered_headers = " ".join(f"{k}:{v}" for k, v in headers.items()).lower() if headers else ""
    if "datadome" in lowered_headers or "x-datadome" in lowered_headers:
        return "datadome"
    try:
        source = driver.page_source or ""
    except Exception:
        return None
    lowered = source.lower()
    if "captcha" in lowered or "captcha-delivery.com" in lowered or "geo.captcha-delivery.com" in lowered:
        return "captcha"
    if "cloudflare" in lowered or "attention required" in lowered:
        return "cloudflare"
    if "access denied" in lowered:
        return "access_denied"
    if "<title>allegro.pl</title>" in lowered and "data-serialize-box-id" not in lowered:
        return "blocked_minimal_page"
    return None


def _detect_no_results_text(driver: WebDriver) -> bool:
    try:
        source = driver.page_source or ""
    except Exception:
        return False
    lowered = source.lower()
    markers = (
        "brak wynik\u00f3w",
        "nie znale\u017ali\u015bmy",
        "0 wynik\u00f3w",
        "no results",
        "no items found",
    )
    return any(marker in lowered for marker in markers)


def _status_indicates_block(request_meta: Dict[str, Any]) -> bool:
    if not request_meta:
        return False
    try:
        status_code = int(request_meta.get("status_code")) if request_meta.get("status_code") is not None else None
    except Exception:
        status_code = None
    if status_code is None:
        return False
    if status_code in (401, 403, 429):
        return True
    return status_code >= 500


def _status_indicates_not_found(request_meta: Dict[str, Any]) -> bool:
    if not request_meta:
        return False
    try:
        status_code = int(request_meta.get("status_code")) if request_meta.get("status_code") is not None else None
    except Exception:
        return False
    return status_code == 404


def _normalize_offer_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return urljoin(_ALLEGRO_BASE_URL, url)


def _offer_link_from_dom(driver: WebDriver) -> Optional[str]:
    selectors = (
        'a[href*="/oferta/"]',
        'a[href*="allegro.pl/oferta/"]',
    )
    for selector in selectors:
        try:
            link = driver.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        href = link.get_attribute("href")
        if href:
            return href
    return None


def _request_metadata(driver: Optional[WebDriver]) -> Dict[str, Any]:
    if driver is None:
        return {}
    try:
        requests = list(getattr(driver, "requests", []) or [])
    except Exception:
        return {}
    target = None
    for req in reversed(requests):
        try:
            url = req.url
        except Exception:
            continue
        if not url or "allegro.pl" not in url:
            continue
        if "/listing" in url or "listView" in url:
            target = req
            break
        if target is None:
            target = req
    if not target:
        return {}
    status_code = None
    headers = None
    try:
        if getattr(target, "response", None):
            status_code = getattr(target.response, "status_code", None)
            headers = dict(getattr(target.response, "headers", {}) or {})
    except Exception:
        status_code = None
    return {
        "url": getattr(target, "url", None),
        "method": getattr(target, "method", None),
        "status_code": status_code,
        "response_headers": headers,
    }


def get_runtime_info() -> dict:
    chrome_path = _resolve_chrome_binary()
    driver_path = _resolve_chromedriver_path()
    info = {
        "arch": platform.machine(),
        "python": platform.python_version(),
        "chrome_path": chrome_path,
        "chromedriver_path": driver_path,
        "chrome_version": _binary_version(chrome_path),
        "chromedriver_version": _binary_version(driver_path),
        "errors": [],
    }
    if not chrome_path:
        info["errors"].append("chrome_binary_not_found")
    if not driver_path:
        info["errors"].append("chromedriver_not_found")
    return info


def get_driver_debug_info() -> Dict[str, Any]:
    if _LAST_DRIVER_DEBUG:
        return dict(_LAST_DRIVER_DEBUG)
    return {
        "status": "not_initialized",
        "scraper_mode": get_scraper_mode(),
        "user_data_dir": os.getenv("SELENIUM_USER_DATA_DIR"),
        "profile_dir": os.getenv("SELENIUM_PROFILE_DIR"),
        "chrome_args": [],
        "user_agent": None,
        "navigator_webdriver": None,
        "browser_version": None,
    }


def _update_driver_debug(
    driver: WebDriver,
    options: Options,
    profile_dir_override: Optional[str] = None,
    proxy_info: Optional[Dict[str, Any]] = None,
    fingerprint: Optional[SeleniumFingerprint] = None,
) -> None:
    debug: Dict[str, Any] = {
        "status": "ok",
        "scraper_mode": get_scraper_mode(),
        "user_data_dir": profile_dir_override or os.getenv("SELENIUM_USER_DATA_DIR"),
        "profile_dir": os.getenv("SELENIUM_PROFILE_DIR"),
        "chrome_args": list(getattr(options, "arguments", [])),
        "user_agent": None,
        "navigator_webdriver": None,
        "browser_version": None,
    }
    if proxy_info:
        debug["proxy_id"] = proxy_info.get("proxy_id")
        debug["proxy_source"] = proxy_info.get("proxy_source")
    if fingerprint:
        debug["fingerprint_id"] = fingerprint.fingerprint_id
        debug["profile_mode"] = fingerprint.profile_mode
        debug["profile_reuse_count"] = fingerprint.profile_reuse_count
        debug["profile_rotate_after"] = fingerprint.profile_rotate_after
    try:
        debug["user_agent"] = driver.execute_script("return navigator.userAgent")
    except Exception:
        pass
    try:
        debug["navigator_webdriver"] = driver.execute_script("return navigator.webdriver")
    except Exception:
        pass
    try:
        caps = getattr(driver, "capabilities", {}) or {}
        debug["browser_version"] = caps.get("browserVersion") or caps.get("browser_version")
    except Exception:
        pass

    _LAST_DRIVER_DEBUG.clear()
    _LAST_DRIVER_DEBUG.update(debug)
    user_agent = debug.get("user_agent")
    user_agent_hash = ua_hash(user_agent)
    user_agent_version = ua_version(user_agent)
    logger.info(
        "local_scraper driver debug mode=%s user_data_dir=%s profile_dir=%s chrome_args=%s ua_hash=%s ua_version=%s webdriver=%s browser_version=%s proxy_id=%s proxy_source=%s profile_mode=%s profile_reuse=%s/%s fingerprint_id=%s",
        debug.get("scraper_mode"),
        debug.get("user_data_dir"),
        debug.get("profile_dir"),
        debug.get("chrome_args"),
        user_agent_hash,
        user_agent_version,
        debug.get("navigator_webdriver"),
        debug.get("browser_version"),
        debug.get("proxy_id"),
        debug.get("proxy_source"),
        debug.get("profile_mode"),
        debug.get("profile_reuse_count"),
        debug.get("profile_rotate_after"),
        debug.get("fingerprint_id"),
    )


def _create_driver() -> Tuple[WebDriver, Optional[SeleniumFingerprint]]:
    driver_path = _resolve_chromedriver_path()
    if not driver_path:
        raise RuntimeError("chromedriver_not_found")
    fingerprint = get_selenium_fingerprint()
    if fingerprint:
        count, threshold = _bump_rotation_counter(fingerprint.rotated)
        logger.info(
            "local_scraper fingerprint fingerprint_id=%s ua_hash=%s ua_version=%s viewport=%sx%s lang=%s profile_mode=%s profile_reuse=%s/%s profile_rotated=%s rotated=%s ua_source=%s requests_since_rotate=%s rotate_after=%s",
            fingerprint.fingerprint_id,
            fingerprint.ua_hash,
            fingerprint.ua_version,
            fingerprint.viewport[0],
            fingerprint.viewport[1],
            fingerprint.lang,
            fingerprint.profile_mode,
            fingerprint.profile_reuse_count,
            fingerprint.profile_rotate_after,
            fingerprint.profile_rotated,
            fingerprint.rotated,
            fingerprint.ua_source,
            count,
            threshold,
        )
    proxy_config = get_selenium_proxy(fingerprint)
    proxy_info: Dict[str, Any] = {}
    if proxy_config:
        proxy_info = {
            "proxy_id": proxy_config.proxy_id,
            "proxy_source": proxy_config.source,
            "proxy_url": proxy_config.proxy_url,
            "proxy_rotated": proxy_config.rotated,
        }
        logger.info(
            "local_scraper proxy source=%s proxy_id=%s rotated=%s",
            proxy_config.source,
            proxy_config.proxy_id,
            proxy_config.rotated,
        )

    def _start_driver(options: Options) -> WebDriver:
        chromedriver_log_path = os.getenv("SELENIUM_CHROMEDRIVER_LOG_PATH")
        log_output = None
        if chromedriver_log_path:
            chromedriver_log_file = Path(chromedriver_log_path).expanduser()
            chromedriver_log_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                chromedriver_log_file.touch(exist_ok=True)
            except Exception:
                pass
            log_output = str(chromedriver_log_file)
        service = Service(executable_path=driver_path, log_output=log_output)
        seleniumwire_options = None
        proxy_raw = proxy_config.proxy_url if proxy_config else ""
        if proxy_raw:
            proxy_url = proxy_raw if "://" in proxy_raw else f"socks5://{proxy_raw}"
            parsed = urlparse(proxy_url)
            if not parsed.hostname or parsed.port is None:
                raise RuntimeError(
                    "SELENIUM_PROXY must include host and port, e.g. user:pass@host:port or "
                    "socks5://user:pass@host:port"
                )
            seleniumwire_options = {
                "proxy": {
                    "http": proxy_url,
                    "https": proxy_url,
                    "no_proxy": "localhost,127.0.0.1",
                },
                "verify_ssl": False,
            }
        if seleniumwire_options:
            return webdriver.Chrome(service=service, options=options, seleniumwire_options=seleniumwire_options)
        return webdriver.Chrome(service=service, options=options)

    last_exc: Optional[Exception] = None

    def _try_start(label: str, options: Options) -> Optional[WebDriver]:
        nonlocal last_exc
        try:
            return _start_driver(options)
        except (SessionNotCreatedException, WebDriverException) as exc:
            last_exc = exc
            logger.warning("Chrome start failed (%s): %s", label, exc)
            return None

    forced_profile_dir: Optional[str] = None
    if fingerprint and fingerprint.profile_dir:
        forced_profile_dir = fingerprint.profile_dir
        logger.warning("Using rotating profile dir=%s", forced_profile_dir)
    elif _env_flag_enabled(os.getenv("SELENIUM_FORCE_TEMP_PROFILE")):
        forced_profile_dir = _get_forced_profile_dir()
        logger.warning("Using forced profile dir=%s", forced_profile_dir)
    elif _FORCED_PROFILE_DIR:
        forced_profile_dir = _FORCED_PROFILE_DIR
        logger.warning("Using fallback profile dir=%s", forced_profile_dir)

    options = _build_chrome_options(user_data_dir_override=forced_profile_dir, fingerprint=fingerprint)
    driver = _try_start("forced_profile" if forced_profile_dir else "default", options)

    if driver is None and forced_profile_dir is not None:
        fallback_dir = _new_temp_profile_dir()
        logger.warning("Retrying with new forced profile dir=%s", fallback_dir)
        options = _build_chrome_options(user_data_dir_override=fallback_dir, fingerprint=fingerprint)
        driver = _try_start("forced_profile_retry", options)
        if driver is not None:
            _FORCED_PROFILE_DIR = fallback_dir

    if driver is None and forced_profile_dir is None and _env_flag_enabled(os.getenv("SELENIUM_PROFILE_FALLBACK")):
        fallback_dir = _new_temp_profile_dir()
        logger.warning("Retrying with fallback profile dir=%s", fallback_dir)
        options = _build_chrome_options(user_data_dir_override=fallback_dir, fingerprint=fingerprint)
        driver = _try_start("fallback_profile", options)
        if driver is not None:
            _FORCED_PROFILE_DIR = fallback_dir

    if driver is None and not is_headless_mode():
        fallback_dir = _FORCED_PROFILE_DIR or _new_temp_profile_dir()
        logger.warning("Retrying in headless mode with profile dir=%s", fallback_dir)
        options = _build_chrome_options(
            user_data_dir_override=fallback_dir,
            headless_override=True,
            fingerprint=fingerprint,
        )
        driver = _try_start("fallback_headless", options)

    if driver is None:
        raise last_exc if last_exc is not None else RuntimeError("chromedriver_start_failed")
    _apply_stealth(driver, fingerprint)
    try:
        if fingerprint:
            driver.set_window_size(fingerprint.viewport[0], fingerprint.viewport[1])
        else:
            driver.maximize_window()
    except Exception:
        pass
    if fingerprint and fingerprint.timezone:
        try:
            driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": fingerprint.timezone})
        except Exception:
            pass
    try:
        if proxy_info:
            setattr(driver, "_proxy_info", proxy_info)
    except Exception:
        pass
    _update_driver_debug(
        driver,
        options,
        profile_dir_override=forced_profile_dir,
        proxy_info=proxy_info,
        fingerprint=fingerprint,
    )
    return driver, fingerprint


def _accept_cookies(driver: WebDriver) -> None:
    try:
        consent_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-role="accept-consent"]'))
        )
        consent_btn.click()
    except Exception:
        return


def _wait_for_listing_data(driver: WebDriver) -> Dict:
    wait_seconds = int(os.getenv("LOCAL_SCRAPER_LISTING_TIMEOUT", "50"))

    def _probe(drv: WebDriver):
        data = _listing_state_from_scripts(drv)
        return data or False

    data = WebDriverWait(driver, wait_seconds).until(_probe)
    if isinstance(data, dict):
        return data
    return {}


def _parse_sold_count(label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    match = re.search(r"([0-9][0-9\\s]*)", str(label))
    if not match:
        return None
    try:
        return int(match.group(1).replace(" ", ""))
    except Exception:
        return None


def _normalize_gtin(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r"\\D+", "", str(value))
    if not digits:
        return None
    return digits


def _gtin_matches(a: Optional[str], b: Optional[str]) -> bool:
    a_norm = _normalize_gtin(a)
    b_norm = _normalize_gtin(b)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm:
        return True
    max_len = max(len(a_norm), len(b_norm))
    if a_norm.zfill(max_len) == b_norm.zfill(max_len):
        return True
    return a_norm.lstrip("0") == b_norm.lstrip("0")


def _extract_offers(elements: List[Dict]) -> Tuple[List[Dict], Optional[str], Optional[str], Optional[int]]:
    offers: List[Dict] = []
    product_title: Optional[str] = None
    product_url: Optional[str] = None
    category_sold_count: Optional[int] = None

    for item in elements:
        title_text = None
        raw_title = item.get("productSeoLink", {}).get("label") or item.get("title")
        if isinstance(raw_title, dict):
            title_text = raw_title.get("text")
        elif isinstance(raw_title, str):
            title_text = raw_title

        if not product_title and title_text:
            product_title = title_text

        offer_url = item.get("url") or item.get("productSeoLink", {}).get("url")
        if not product_url and offer_url:
            product_url = offer_url

        price_value = None
        try:
            price_value = item["price"]["mainPrice"]["amount"]
        except Exception:
            price_value = None
        price = float(price_value) if price_value is not None else None

        sold_label = None
        try:
            sold_label = item.get("productPopularity", {}).get("label")
        except Exception:
            sold_label = None
        sold_count = _parse_sold_count(sold_label)
        if category_sold_count is None and sold_count is not None:
            category_sold_count = sold_count

        seller_info = item.get("seller") or {}
        seller_name = seller_info.get("login") or seller_info.get("title")

        offers.append(
            {
                "seller_name": seller_name,
                "price": price,
                "sold_count": sold_count,
                "offer_url": offer_url,
                "is_promo": bool(item.get("promotionEmphasized") or item.get("promoted")),
                "raw": {"productId": item.get("productDetails", {}).get("productId")},
            }
        )

    return offers, product_title, product_url, category_sold_count


def _validate_ean(driver: WebDriver, product_url: Optional[str]) -> Optional[str]:
    if not product_url:
        return None
    try:
        driver.get(_normalize_offer_url(product_url))
        _accept_cookies(driver)
        meta_tag = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'meta[itemprop="gtin"]'))
        )
        return meta_tag.get_attribute("content")
    except Exception:
        pass
    try:
        offer_url = _offer_link_from_dom(driver)
        if not offer_url:
            return None
        driver.get(_normalize_offer_url(offer_url))
        _accept_cookies(driver)
        meta_tag = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'meta[itemprop="gtin"]'))
        )
        return meta_tag.get_attribute("content")
    except Exception:
        return None


def _retry_backoff_seconds(attempt: int) -> float:
    try:
        base = float(os.getenv("LOCAL_SCRAPER_RETRY_BACKOFF", "2.0"))
    except Exception:
        base = 2.0
    return min(10.0, max(0.5, base) * max(1, attempt))


def _build_result_payload(
    *,
    ean: str,
    scraped_at: str,
    fingerprint_id: Optional[str],
    vnc_active: bool,
    blocked: bool,
    not_found: bool,
    error: Optional[str],
    product_title: Optional[str],
    product_url: Optional[str],
    offers: Optional[List[Dict]],
    category_sold_count: Optional[int],
    offers_total_sold_count: Optional[int],
    lowest_price: Optional[float],
    second_lowest_price: Optional[float],
    original_ean: Optional[str],
    stage_durations: Optional[Dict[str, Any]],
    request_meta: Optional[Dict[str, Any]],
    attempt: int,
    max_attempts: int,
    block_reason: Optional[str],
    proxy_info: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    offers = offers or []
    request_meta = request_meta or {}
    proxy_info = proxy_info or {}
    stage_durations = stage_durations or {}
    request_status = request_meta.get("status_code")
    request_url = request_meta.get("url")
    return {
        "ean": str(ean),
        "product_title": product_title,
        "product_url": product_url,
        "category_sold_count": category_sold_count,
        "offers_total_sold_count": offers_total_sold_count,
        "lowest_price": lowest_price,
        "second_lowest_price": second_lowest_price,
        "offers": offers,
        "not_found": bool(not_found),
        "blocked": bool(blocked),
        "scraped_at": scraped_at,
        "source": "local_scraper",
        "error": error,
        "price": lowest_price,
        "sold_count": offers_total_sold_count,
        "original_ean": original_ean,
        "fingerprint_id": fingerprint_id,
        "vnc_active": vnc_active,
        "block_reason": block_reason,
        "request_status_code": request_status,
        "request_url": request_url,
        "stage_durations": stage_durations,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "proxy_id": proxy_info.get("proxy_id"),
        "proxy_source": proxy_info.get("proxy_source"),
    }


def _platform_from_user_agent(user_agent: Optional[str]) -> str:
    ua = user_agent or ""
    if "Mac OS X" in ua or "Macintosh" in ua:
        return "MacIntel"
    if "Win64" in ua or "Windows" in ua:
        return "Win32"
    if "Linux" in ua:
        return "Linux x86_64"
    return "Win32"


def _apply_stealth(driver: WebDriver, fingerprint: Optional[SeleniumFingerprint]) -> None:
    if not fingerprint:
        return
    platform_name = _platform_from_user_agent(fingerprint.user_agent)
    languages = []
    for raw in (fingerprint.accept_language or "").split(","):
        lang = raw.split(";")[0].strip()
        if lang:
            languages.append(lang)
    if not languages and fingerprint.lang:
        languages = [fingerprint.lang]
    if not languages:
        languages = ["pl-PL", "pl"]
    script = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'platform', {get: () => '%s'});
Object.defineProperty(navigator, 'language', {get: () => '%s'});
Object.defineProperty(navigator, 'languages', {get: () => %s});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: 'default', onchange: null })
      : originalQuery(parameters)
  );
}
""" % (
        platform_name.replace("'", ""),
        (languages[0] if languages else "pl-PL").replace("'", ""),
        json.dumps(languages),
    )
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {
                "userAgent": fingerprint.user_agent,
                "acceptLanguage": fingerprint.accept_language,
                "platform": platform_name,
            },
        )
    except Exception:
        pass


def _scrape_attempt(ean: str, scraped_at: str, attempt: int, max_attempts: int) -> dict:
    driver: Optional[WebDriver] = None
    fingerprint: Optional[SeleniumFingerprint] = None
    fingerprint_id: Optional[str] = None
    vnc_active = _vnc_enabled()
    stage_durations: Dict[str, Any] = {}
    request_meta: Dict[str, Any] = {}
    proxy_info: Dict[str, Any] = {}
    try:
        driver, fingerprint = _create_driver()
        fingerprint_id = fingerprint.fingerprint_id if fingerprint else None
        proxy_info = getattr(driver, "_proxy_info", {}) or {}
        driver.set_page_load_timeout(int(os.getenv("LOCAL_SCRAPER_PAGELOAD_TIMEOUT", "45")))
        page_load_started = time.monotonic()
        driver.get(f"https://allegro.pl/listing?string={ean}")
        stage_durations["page_load_seconds"] = round(time.monotonic() - page_load_started, 2)
        request_meta = _request_metadata(driver)
        if _status_indicates_block(request_meta):
            block_reason = _detect_block_reason(driver, request_meta)
            logger.warning(
                "local_scraper blocked_early ean=%s attempt=%s/%s block_reason=%s status=%s fingerprint_id=%s proxy_id=%s",
                ean,
                attempt,
                max_attempts,
                block_reason,
                request_meta.get("status_code"),
                fingerprint_id,
                proxy_info.get("proxy_id"),
            )
            return _build_result_payload(
                ean=ean,
                scraped_at=scraped_at,
                fingerprint_id=fingerprint_id,
                vnc_active=vnc_active,
                blocked=True,
                not_found=False,
                error=f"blocked:{block_reason or 'status'}",
                product_title=None,
                product_url=None,
                offers=[],
                category_sold_count=None,
                offers_total_sold_count=None,
                lowest_price=None,
                second_lowest_price=None,
                original_ean=None,
                stage_durations=stage_durations,
                request_meta=request_meta,
                attempt=attempt,
                max_attempts=max_attempts,
                block_reason=block_reason or "status_block",
                proxy_info=proxy_info,
            )
        _accept_cookies(driver)
        wait_started = time.monotonic()
        data = _wait_for_listing_data(driver)
        stage_durations["listing_wait_seconds"] = round(time.monotonic() - wait_started, 2)
        request_meta = request_meta or _request_metadata(driver)
        elements = data.get("__listing_StoreState", {}).get("items", {}).get("elements", []) or []
        block_reason = _detect_block_reason(driver, request_meta)
        if _status_indicates_not_found(request_meta) or (not elements and _detect_no_results_text(driver)):
            logger.info(
                "local_scraper not_found ean=%s attempt=%s/%s status=%s fingerprint_id=%s proxy_id=%s",
                ean,
                attempt,
                max_attempts,
                request_meta.get("status_code"),
                fingerprint_id,
                proxy_info.get("proxy_id"),
            )
            return _build_result_payload(
                ean=ean,
                scraped_at=scraped_at,
                fingerprint_id=fingerprint_id,
                vnc_active=vnc_active,
                blocked=False,
                not_found=True,
                error=None,
                product_title=None,
                product_url=None,
                offers=[],
                category_sold_count=None,
                offers_total_sold_count=None,
                lowest_price=None,
                second_lowest_price=None,
                original_ean=None,
                stage_durations=stage_durations,
                request_meta=request_meta,
                attempt=attempt,
                max_attempts=max_attempts,
                block_reason=None,
                proxy_info=proxy_info,
            )

        if block_reason:
            logger.warning(
                "local_scraper blocked ean=%s attempt=%s/%s block_reason=%s status=%s fingerprint_id=%s proxy_id=%s",
                ean,
                attempt,
                max_attempts,
                block_reason,
                request_meta.get("status_code"),
                fingerprint_id,
                proxy_info.get("proxy_id"),
            )
            return _build_result_payload(
                ean=ean,
                scraped_at=scraped_at,
                fingerprint_id=fingerprint_id,
                vnc_active=vnc_active,
                blocked=True,
                not_found=False,
                error=f"blocked:{block_reason}",
                product_title=None,
                product_url=None,
                offers=[],
                category_sold_count=None,
                offers_total_sold_count=None,
                lowest_price=None,
                second_lowest_price=None,
                original_ean=None,
                stage_durations=stage_durations,
                request_meta=request_meta,
                attempt=attempt,
                max_attempts=max_attempts,
                block_reason=block_reason,
                proxy_info=proxy_info,
            )

        offers, product_title, product_url, category_sold_count = _extract_offers(elements)
        if not product_url:
            product_url = _offer_link_from_dom(driver)
        product_url = _normalize_offer_url(product_url)
        original_ean = _validate_ean(driver, product_url)

        prices = [o["price"] for o in offers if o.get("price") is not None]
        sorted_prices = sorted(prices)
        lowest_price = sorted_prices[0] if sorted_prices else None
        second_lowest_price = sorted_prices[1] if len(sorted_prices) > 1 else None
        offers_total_sold_count = None
        try:
            sold_values = [o["sold_count"] for o in offers if o.get("sold_count") is not None]
            offers_total_sold_count = sum(sold_values) if sold_values else None
        except Exception:
            offers_total_sold_count = None

        not_found = not offers
        if original_ean and not _gtin_matches(original_ean, str(ean)):
            not_found = True

        logger.info(
            "local_scraper outcome ean=%s attempt=%s/%s not_found=%s offers=%s lowest_price=%s status=%s fingerprint_id=%s proxy_id=%s",
            ean,
            attempt,
            max_attempts,
            not_found,
            len(offers),
            lowest_price,
            request_meta.get("status_code"),
            fingerprint_id,
            proxy_info.get("proxy_id"),
        )

        return _build_result_payload(
            ean=ean,
            scraped_at=scraped_at,
            fingerprint_id=fingerprint_id,
            vnc_active=vnc_active,
            blocked=False,
            not_found=bool(not_found),
            error=None,
            product_title=product_title,
            product_url=product_url,
            offers=offers,
            category_sold_count=category_sold_count,
            offers_total_sold_count=offers_total_sold_count,
            lowest_price=lowest_price,
            second_lowest_price=second_lowest_price,
            original_ean=original_ean,
            stage_durations=stage_durations,
            request_meta=request_meta,
            attempt=attempt,
            max_attempts=max_attempts,
            block_reason=None,
            proxy_info=proxy_info,
        )
    except TimeoutException:
        if not request_meta:
            request_meta = _request_metadata(driver)
        block_reason = _detect_block_reason(driver, request_meta)
        state = _listing_state_from_scripts(driver) if driver else None
        elements_count = _listing_elements_count(state)
        not_found = (elements_count == 0 or _detect_no_results_text(driver)) and not block_reason
        blocked = bool(block_reason or _status_indicates_block(request_meta))
        error_msg = f"blocked:{block_reason}" if block_reason else "timeout"
        logger.warning(
            "local_scraper timeout ean=%s attempt=%s/%s stage=page_load status=%s block_reason=%s not_found=%s fingerprint_id=%s proxy_id=%s",
            ean,
            attempt,
            max_attempts,
            request_meta.get("status_code"),
            block_reason,
            not_found,
            fingerprint_id,
            proxy_info.get("proxy_id"),
        )
        return _build_result_payload(
            ean=ean,
            scraped_at=scraped_at,
            fingerprint_id=fingerprint_id,
            vnc_active=vnc_active,
            blocked=blocked,
            not_found=not_found,
            error=error_msg,
            product_title=None,
            product_url=None,
            offers=[],
            category_sold_count=None,
            offers_total_sold_count=None,
            lowest_price=None,
            second_lowest_price=None,
            original_ean=None,
            stage_durations=stage_durations,
            request_meta=request_meta,
            attempt=attempt,
            max_attempts=max_attempts,
            block_reason=block_reason,
            proxy_info=proxy_info,
        )
    except Exception as exc:
        if not request_meta:
            request_meta = _request_metadata(driver)
        block_reason = _detect_block_reason(driver, request_meta)
        logger.exception(
            "local_scraper failed ean=%s attempt=%s/%s block_reason=%s status=%s fingerprint_id=%s proxy_id=%s",
            ean,
            attempt,
            max_attempts,
            block_reason,
            request_meta.get("status_code"),
            fingerprint_id,
            proxy_info.get("proxy_id"),
        )
        return _build_result_payload(
            ean=ean,
            scraped_at=scraped_at,
            fingerprint_id=fingerprint_id,
            vnc_active=vnc_active,
            blocked=bool(block_reason),
            not_found=False,
            error=str(exc),
            product_title=None,
            product_url=None,
            offers=[],
            category_sold_count=None,
            offers_total_sold_count=None,
            lowest_price=None,
            second_lowest_price=None,
            original_ean=None,
            stage_durations=stage_durations,
            request_meta=request_meta,
            attempt=attempt,
            max_attempts=max_attempts,
            block_reason=block_reason,
            proxy_info=proxy_info,
        )
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def scrape_single_ean(ean: str) -> dict:
    """
    Scrape Allegro for a single EAN using Selenium and return structured data with retries on transient blocks.
    """
    scraped_at = _now_iso()
    try:
        attempts = int(os.getenv("LOCAL_SCRAPER_MAX_ATTEMPTS", "2"))
    except Exception:
        attempts = 2
    attempts = max(1, attempts)
    last_result: Optional[dict] = None
    for attempt in range(1, attempts + 1):
        result = _scrape_attempt(ean, scraped_at, attempt, attempts)
        last_result = result
        blocked = bool(result.get("blocked"))
        error = result.get("error")
        not_found = bool(result.get("not_found"))
        if not blocked and (not error or not error.startswith("timeout")):
            return result
        if not_found:
            return result
        if attempt < attempts:
            if blocked:
                force_rotate_selenium_fingerprint()
                force_rotate_selenium_proxy()
                force_rotate_profile()
            backoff = _retry_backoff_seconds(attempt) + random.uniform(0, 0.7)
            logger.info(
                "local_scraper retry ean=%s attempt=%s/%s blocked=%s error=%s backoff=%.2fs",
                ean,
                attempt,
                attempts,
                blocked,
                error,
                backoff,
            )
            time.sleep(backoff)
            continue
        return result
    return last_result or {
        "ean": str(ean),
        "product_title": None,
        "product_url": None,
        "category_sold_count": None,
        "offers_total_sold_count": None,
        "lowest_price": None,
        "second_lowest_price": None,
        "offers": [],
        "not_found": False,
        "blocked": True,
        "scraped_at": scraped_at,
        "source": "local_scraper",
        "error": "unknown_error",
        "fingerprint_id": None,
        "vnc_active": _vnc_enabled(),
    }


def getProductDetail(eans: Iterable[str | int]) -> List[dict]:
    """Optional helper for manual debugging."""
    results: List[dict] = []
    for ean in eans:
        detail = scrape_single_ean(str(ean))
        print(f"Product detail: {detail}")
        results.append(detail)
    return results


if __name__ == "__main__":
    sample_eans = [3614273955539, 194251004068, 5904183600666, 59041836006611]
    getProductDetail(sample_eans)
