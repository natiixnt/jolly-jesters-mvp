from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from main import scrape_single_ean

app = FastAPI(title="Local Allegro Selenium Scraper", version="1.0.0")


class ScrapeRequest(BaseModel):
    ean: str


class ScrapeResponse(BaseModel):
    ean: str
    price: Optional[float]
    sold_count: Optional[int]
    not_found: bool
    last_checked_at: str
    product_url: Optional[str] = None
    product_title: Optional[str] = None
    original_ean: Optional[str] = None
    allegro_lowest_price: Optional[float] = None
    source: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}


@app.post("/scrape", response_model=ScrapeResponse)
def scrape(req: ScrapeRequest):
    try:
        detail = scrape_single_ean(req.ean)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    price = detail.get("allegro_lowest_price")
    sold = detail.get("sold_count")
    not_found = bool(detail.get("not_found", False))

    return ScrapeResponse(
        ean=req.ean,
        price=price,
        sold_count=sold,
        not_found=not_found,
        last_checked_at=detail.get("last_checked_at", datetime.now().isoformat()),
        product_url=detail.get("product_url"),
        product_title=detail.get("product_title"),
        original_ean=detail.get("original_ean"),
        allegro_lowest_price=detail.get("allegro_lowest_price"),
        source=detail.get("source"),
    )
