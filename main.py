import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumbase import Driver


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _create_driver() -> Driver:
    headed_env = os.getenv("SELENIUM_HEADED", "true").strip().lower()
    headed = headed_env not in {"0", "false", "no", "off"}
    driver = Driver(uc=True, headed=headed)
    try:
        driver.maximize_window()
    except Exception:
        pass
    return driver


def _accept_cookies(driver: Driver) -> None:
    try:
        consent_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-role="accept-consent"]'))
        )
        consent_btn.click()
    except Exception:
        return


def _wait_for_listing_data(driver: Driver) -> Dict:
    script_tag = WebDriverWait(driver, 40).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, 'script[data-serialize-box-id="EHg7vYMJTQ275owpOcr4Lg=="]')
        )
    )
    data = json.loads(script_tag.get_attribute("innerHTML"))
    return data


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


def _validate_ean(driver: Driver, product_url: Optional[str]) -> Optional[str]:
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
    driver = _create_driver()
    scraped_at = _now_iso()

    try:
        driver.get(f"https://allegro.pl/listing?string={ean}")
        _accept_cookies(driver)
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
            "error": "timeout",
        }
    except Exception as exc:
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
