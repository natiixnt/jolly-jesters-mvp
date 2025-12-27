import json
import logging
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


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


def _create_driver() -> WebDriver:
    headed_env = os.getenv("SELENIUM_HEADED")
    if headed_env is None:
        headed = bool(os.getenv("DISPLAY"))
    else:
        headed = headed_env.strip().lower() not in {"0", "false", "no", "off"}
    chrome_path = _resolve_chrome_binary()
    driver_path = _resolve_chromedriver_path()
    if not driver_path:
        raise RuntimeError("chromedriver_not_found")

    options = Options()
    if chrome_path:
        options.binary_location = chrome_path
    if not headed:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
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
    user_data_dir = os.getenv("SELENIUM_USER_DATA_DIR")
    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
    profile_dir = os.getenv("SELENIUM_PROFILE_DIR")
    if profile_dir:
        options.add_argument(f"--profile-directory={profile_dir}")

    service = Service(executable_path=driver_path)
    driver = webdriver.Chrome(service=service, options=options)
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
        block_reason = _detect_block_reason(driver)
        if block_reason:
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
                "error": f"blocked:{block_reason}",
            }
        _accept_cookies(driver)
        block_reason = _detect_block_reason(driver)
        if block_reason:
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
                "error": f"blocked:{block_reason}",
            }
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
        if original_ean and original_ean != str(ean):
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
