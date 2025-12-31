import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
from pathlib import Path
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)
_LAST_DRIVER_DEBUG: Dict[str, Any] = {}
_FORCED_PROFILE_DIR: Optional[str] = None


def _env_flag_enabled(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_flag_disabled(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"0", "false", "no", "off"}


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
    options.add_argument("--window-size=1280,800")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=pl-PL")
    user_agent = os.getenv("SELENIUM_USER_AGENT")
    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")
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


def _detect_block_reason(driver: WebDriver) -> Optional[str]:
    try:
        source = driver.page_source or ""
    except Exception:
        return None
    lowered = source.lower()
    if "captcha-delivery.com" in lowered or "geo.captcha-delivery.com" in lowered:
        return "captcha"
    if "cloudflare" in lowered or "attention required" in lowered:
        return "cloudflare"
    if "access denied" in lowered:
        return "access_denied"
    if "<title>allegro.pl</title>" in lowered and "data-serialize-box-id" not in lowered:
        return "blocked_minimal_page"
    return None


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


def _update_driver_debug(driver: WebDriver, options: Options) -> None:
    debug: Dict[str, Any] = {
        "status": "ok",
        "scraper_mode": get_scraper_mode(),
        "user_data_dir": os.getenv("SELENIUM_USER_DATA_DIR"),
        "profile_dir": os.getenv("SELENIUM_PROFILE_DIR"),
        "chrome_args": list(getattr(options, "arguments", [])),
        "user_agent": None,
        "navigator_webdriver": None,
        "browser_version": None,
    }
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
    logger.info(
        "local_scraper driver debug mode=%s user_data_dir=%s profile_dir=%s chrome_args=%s user_agent=%s webdriver=%s browser_version=%s",
        debug.get("scraper_mode"),
        debug.get("user_data_dir"),
        debug.get("profile_dir"),
        debug.get("chrome_args"),
        debug.get("user_agent"),
        debug.get("navigator_webdriver"),
        debug.get("browser_version"),
    )


def _create_driver() -> WebDriver:
    driver_path = _resolve_chromedriver_path()
    if not driver_path:
        raise RuntimeError("chromedriver_not_found")

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
    if _env_flag_enabled(os.getenv("SELENIUM_FORCE_TEMP_PROFILE")):
        forced_profile_dir = _get_forced_profile_dir()
        logger.warning("Using forced profile dir=%s", forced_profile_dir)
    elif _FORCED_PROFILE_DIR:
        forced_profile_dir = _FORCED_PROFILE_DIR
        logger.warning("Using fallback profile dir=%s", forced_profile_dir)

    options = _build_chrome_options(user_data_dir_override=forced_profile_dir)
    driver = _try_start("forced_profile" if forced_profile_dir else "default", options)

    if driver is None and forced_profile_dir is not None:
        fallback_dir = _new_temp_profile_dir()
        logger.warning("Retrying with new forced profile dir=%s", fallback_dir)
        options = _build_chrome_options(user_data_dir_override=fallback_dir)
        driver = _try_start("forced_profile_retry", options)
        if driver is not None:
            _FORCED_PROFILE_DIR = fallback_dir

    if driver is None and forced_profile_dir is None and _env_flag_enabled(os.getenv("SELENIUM_PROFILE_FALLBACK")):
        fallback_dir = _new_temp_profile_dir()
        logger.warning("Retrying with fallback profile dir=%s", fallback_dir)
        options = _build_chrome_options(user_data_dir_override=fallback_dir)
        driver = _try_start("fallback_profile", options)
        if driver is not None:
            _FORCED_PROFILE_DIR = fallback_dir

    if driver is None and not is_headless_mode():
        fallback_dir = _FORCED_PROFILE_DIR or _new_temp_profile_dir()
        logger.warning("Retrying in headless mode with profile dir=%s", fallback_dir)
        options = _build_chrome_options(user_data_dir_override=fallback_dir, headless_override=True)
        driver = _try_start("fallback_headless", options)

    if driver is None:
        raise last_exc if last_exc is not None else RuntimeError("chromedriver_start_failed")
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
        )
    except Exception:
        pass
    try:
        driver.maximize_window()
    except Exception:
        pass
    _update_driver_debug(driver, options)
    return driver


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
        driver.get(product_url)
        _accept_cookies(driver)
        meta_tag = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'meta[itemprop="gtin"]'))
        )
        return meta_tag.get_attribute("content")
    except Exception:
        return None


def scrape_single_ean(ean: str) -> dict:
    """
    Scrape Allegro for a single EAN using SeleniumBase and return structured data.
    Attempts to mimic the pilot flow: load listing, open product page, and collect offers.
    """
    scraped_at = _now_iso()
    driver: Optional[WebDriver] = None

    try:
        driver = _create_driver()
        driver.set_page_load_timeout(int(os.getenv("LOCAL_SCRAPER_PAGELOAD_TIMEOUT", "45")))
        driver.get(f"https://allegro.pl/listing?string={ean}")
        _accept_cookies(driver)
        # Wait for listing data before deciding about blocks to avoid false positives.
        data = _wait_for_listing_data(driver)
        elements = data.get("__listing_StoreState", {}).get("items", {}).get("elements", []) or []

        offers, product_title, product_url, category_sold_count = _extract_offers(elements)
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

        result = {
            "ean": str(ean),
            "product_title": product_title,
            "product_url": product_url,
            "category_sold_count": category_sold_count,
            "offers_total_sold_count": offers_total_sold_count,
            "lowest_price": lowest_price,
            "second_lowest_price": second_lowest_price,
            "offers": offers,
            "not_found": bool(not_found),
            "blocked": False,
            "scraped_at": scraped_at,
            "source": "local_scraper",
            "error": None,
            # Legacy compatibility keys
            "price": lowest_price,
            "sold_count": offers_total_sold_count,
            "original_ean": original_ean,
        }
        return result
    except TimeoutException:
        block_reason = None
        if driver:
            block_reason = _detect_block_reason(driver)
        error_msg = f"blocked:{block_reason}" if block_reason else "timeout"
        return {
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
            "error": error_msg,
        }
    except Exception as exc:
        logger.exception("Local scraper failed for ean=%s", ean)
        return {
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
            "error": str(exc),
        }
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


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
