import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from main import get_runtime_info, get_scraper_mode, scrape_single_ean

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Local Allegro Selenium Scraper", version="1.0.0")


@app.on_event("startup")
def _log_runtime_info() -> None:
    info = get_runtime_info()
    logger.info(
        "local_scraper config mode=%s user_data_dir=%s profile_dir=%s",
        get_scraper_mode(),
        os.getenv("SELENIUM_USER_DATA_DIR"),
        os.getenv("SELENIUM_PROFILE_DIR"),
    )
    logger.info(
        "local_scraper runtime arch=%s chrome=%s chromedriver=%s chrome_path=%s driver_path=%s errors=%s",
        info.get("arch"),
        info.get("chrome_version"),
        info.get("chromedriver_version"),
        info.get("chrome_path"),
        info.get("chromedriver_path"),
        info.get("errors"),
    )


@app.get("/health")
def health() -> dict:
    info = get_runtime_info()
    status = "ok" if not info.get("errors") else "degraded"
    return {"status": status, "details": info}


class ScrapeRequest(BaseModel):
    ean: str


class Offer(BaseModel):
    seller_name: Optional[str]
    price: Optional[float]
    sold_count: Optional[int]
    offer_url: Optional[str]
    is_promo: bool = False


class ScrapeResponse(BaseModel):
    ean: str
    product_url: Optional[str] = None
    product_title: Optional[str] = None
    category_sold_count: Optional[int] = None
    offers_total_sold_count: Optional[int] = None
    lowest_price: Optional[float] = None
    second_lowest_price: Optional[float] = None
    offers: List[Offer]
    not_found: bool
    blocked: bool
    scraped_at: datetime
    source: str
    error: Optional[str] = None
    # Legacy/compatibility fields
    price: Optional[float] = None
    sold_count: Optional[int] = None
    original_ean: Optional[str] = None


@app.post("/scrape", response_model=ScrapeResponse)
def scrape(req: ScrapeRequest):
    try:
        detail = scrape_single_ean(req.ean)
    except Exception as exc:
        logger.exception("Local scraper crashed for ean=%s", req.ean)
        raise HTTPException(status_code=500, detail=str(exc))

    if detail.get("error"):
        logger.warning("Local scraper error ean=%s error=%s", req.ean, detail.get("error"))

    return ScrapeResponse(
        ean=req.ean,
        product_title=detail.get("product_title"),
        product_url=detail.get("product_url"),
        category_sold_count=detail.get("category_sold_count"),
        offers_total_sold_count=detail.get("offers_total_sold_count"),
        lowest_price=detail.get("lowest_price"),
        second_lowest_price=detail.get("second_lowest_price"),
        offers=detail.get("offers") or [],
        not_found=bool(detail.get("not_found", False)),
        blocked=bool(detail.get("blocked", False)),
        scraped_at=detail.get("scraped_at") or datetime.now().isoformat(),
        source=detail.get("source") or "local_scraper",
        error=detail.get("error"),
        price=detail.get("price"),
        sold_count=detail.get("sold_count"),
        original_ean=detail.get("original_ean"),
    )
