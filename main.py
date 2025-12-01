import json
import time
from datetime import datetime, timezone
from typing import Iterable, List

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumbase import Driver


def scrape_single_ean(ean: str) -> dict:
    """Scrape a single EAN from Allegro using the existing SeleniumBase flow."""
    driver = Driver(uc=True)
    driver.maximize_window()

    try:
        driver.get(f"https://allegro.pl/listing?string={ean}")

        # Accept consent banner if present
        try:
            consent_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-role="accept-consent"]'))
            )
            consent_btn.click()
        except Exception:
            pass

        script_tag = WebDriverWait(driver, 40).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'script[data-serialize-box-id="EHg7vYMJTQ275owpOcr4Lg=="]')
            )
        )
        str_data = script_tag.get_attribute("innerHTML")
        data = json.loads(str_data)

        sold = None
        min_price = None
        product_url = None
        product_title = None

        for item in data["__listing_StoreState"]["items"]["elements"]:
            try:
                sold_value = item["productPopularity"]["label"].split(" ")[0]
                sold = int(sold_value)
            except Exception:
                pass

            try:
                price_value = item["price"]["mainPrice"]["amount"]
                price = float(price_value)
            except Exception:
                price = None

            if min_price is None or (price is not None and price < min_price):
                min_price = price

            if not product_title:
                product_title = item.get("title")

        pein = None
        try:
            title_el = driver.find_element(By.CSS_SELECTOR, ".mgn2_14.m9qz_yp")
            product_title = product_title or title_el.text
            try:
                title_el.click()
            except Exception:
                pass

            time.sleep(2)

            try:
                href = driver.find_element(By.CLASS_NAME, "_1e32a_zIS-q").get_attribute("href")
                if href:
                    product_url = href
                    driver.get(href)
            except Exception:
                pass

            script_tag = WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'meta[itemprop="gtin"]'))
            )
            pein = script_tag.get_attribute("content")
        except Exception:
            pass

        not_found = False
        if (min_price is None and sold is None):
            not_found = True
        elif pein is not None and pein != str(ean):
            not_found = True

        if not_found:
            sold = None
            min_price = None

        detail = {
            "product_url": product_url,
            "ean": str(ean),
            "original_ean": pein,
            "product_title": product_title,
            "allegro_lowest_price": min_price,
            "sold_count": sold,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "source": "scrape",
            "not_found": not_found,
        }
        return detail
    finally:
        driver.quit()


def getProductDetail(eans: Iterable[str | int]) -> List[dict]:
    """Optional helper for multi-EAN debugging; prints results."""
    results: List[dict] = []
    for ean in eans:
        detail = scrape_single_ean(str(ean))
        print(f"Product detail: {detail}")
        results.append(detail)
    return results


if __name__ == "__main__":
    # Debug/manual usage example:
    sample_eans = [3614273955539, 194251004068, 5904183600666, 59041836006611]
    getProductDetail(sample_eans)
