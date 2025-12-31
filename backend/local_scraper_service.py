import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime
from threading import Condition, Lock
from typing import List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from main import get_driver_debug_info, get_runtime_info, get_scraper_mode, scrape_single_ean

logger = logging.getLogger("uvicorn.error")
_WINDOWS_LOCK = Lock()
_WINDOWS_COND = Condition(_WINDOWS_LOCK)
_ACTIVE_WINDOWS = 0


def _normalize_windows(value: object) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return 1


_MAX_WINDOWS = _normalize_windows(os.getenv("LOCAL_SCRAPER_WINDOWS", "1"))


def _get_windows_state() -> Tuple[int, int]:
    with _WINDOWS_COND:
        return _MAX_WINDOWS, _ACTIVE_WINDOWS


def _set_max_windows(value: object) -> int:
    global _MAX_WINDOWS
    normalized = _normalize_windows(value)
    with _WINDOWS_COND:
        _MAX_WINDOWS = normalized
        _WINDOWS_COND.notify_all()
    logger.info("local_scraper windows updated to=%s", normalized)
    return normalized


@contextmanager
def _scrape_slot():
    global _ACTIVE_WINDOWS
    with _WINDOWS_COND:
        while _ACTIVE_WINDOWS >= _MAX_WINDOWS:
            _WINDOWS_COND.wait(timeout=1.0)
        _ACTIVE_WINDOWS += 1
    try:
        yield
    finally:
        with _WINDOWS_COND:
            _ACTIVE_WINDOWS = max(0, _ACTIVE_WINDOWS - 1)
            _WINDOWS_COND.notify_all()

app = FastAPI(title="Local Allegro Selenium Scraper", version="1.0.0")


@app.on_event("startup")
def _log_runtime_info() -> None:
    info = get_runtime_info()
    max_windows, _ = _get_windows_state()
    logger.info(
        "local_scraper config mode=%s user_data_dir=%s profile_dir=%s windows=%s",
        get_scraper_mode(),
        os.getenv("SELENIUM_USER_DATA_DIR"),
        os.getenv("SELENIUM_PROFILE_DIR"),
        max_windows,
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
    max_windows, active = _get_windows_state()
    info = {**info, "local_scraper_windows": max_windows, "active_windows": active}
    return {"status": status, "details": info}


@app.get("/debug")
def debug() -> dict:
    return get_driver_debug_info()


class ScrapeRequest(BaseModel):
    ean: str


class ScraperConfig(BaseModel):
    local_scraper_windows: int


class ScraperConfigResponse(BaseModel):
    local_scraper_windows: int
    active_windows: int


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


@app.get("/config", response_model=ScraperConfigResponse)
def get_config() -> ScraperConfigResponse:
    max_windows, active = _get_windows_state()
    return ScraperConfigResponse(local_scraper_windows=max_windows, active_windows=active)


@app.put("/config", response_model=ScraperConfigResponse)
def update_config(payload: ScraperConfig) -> ScraperConfigResponse:
    max_windows = _set_max_windows(payload.local_scraper_windows)
    _, active = _get_windows_state()
    return ScraperConfigResponse(local_scraper_windows=max_windows, active_windows=active)


@app.post("/scrape", response_model=ScrapeResponse)
def scrape(req: ScrapeRequest):
    try:
        delay = float(os.getenv("LOCAL_SCRAPER_REQUEST_DELAY", "0") or "0")
        if delay > 0:
            logger.info("Local scraper delay ean=%s seconds=%s", req.ean, delay)
            time.sleep(delay)
        with _scrape_slot():
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
