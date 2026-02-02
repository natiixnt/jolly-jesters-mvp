#!/usr/bin/env python
"""
Smoke test runner for both Bright Data (primary) and legacy local scraper.

Usage (inside running docker compose stack):
  docker compose exec backend python backend/scripts/smoke_scraper.py --mode brightdata
  docker compose exec backend python backend/scripts/smoke_scraper.py --mode legacy --eans 5901234123457,5012345678900
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from decimal import Decimal
from typing import List, Tuple

from app.db.session import SessionLocal
from app.models.category import Category
from app.models.enums import ProfitabilityLabel
from app.models.product import Product
from app.services.analysis_service import _ensure_product_state, _persist_market_data, _update_effective_state
from app.utils.brightdata_browser import fetch_via_brightdata
from app.utils.local_scraper_client import fetch_via_local_scraper

DEFAULT_EANS = [
    "5901234123457",
    "5909999999999",
    "5012345678900",
    "4006381333931",
    "7612345000002",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test scraper pipeline")
    parser.add_argument("--mode", choices=["brightdata", "legacy"], default=os.getenv("SCRAPER_MODE", "brightdata"))
    parser.add_argument("--eans", help="Comma-separated EAN list (defaults to built-in sample)")
    return parser.parse_args()


def ensure_category(db: SessionLocal) -> Category:
    category = db.query(Category).filter(Category.name == "Smoke Test").first()
    if category:
        return category
    category = Category(name="Smoke Test", profitability_multiplier=Decimal("1.5"), commission_rate=Decimal("0.1"))
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def ensure_product(db: SessionLocal, category: Category, ean: str) -> Product:
    product = db.query(Product).filter(Product.category_id == category.id, Product.ean == ean).first()
    if product:
        return product
    product = Product(
        category_id=category.id,
        ean=ean,
        name=f"SMOKE-{ean}",
        purchase_price=Decimal("0"),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


async def scrape_one(ean: str, mode: str) -> Tuple[str, float]:
    start = time.monotonic()
    if mode == "legacy":
        result = await fetch_via_local_scraper(ean)
    else:
        result = await fetch_via_brightdata(ean)
    duration = time.monotonic() - start

    # persist into the same tables as the main pipeline
    try:
        with SessionLocal() as db:
            category = ensure_category(db)
            product = ensure_product(db, category, ean)
            state = _ensure_product_state(db, product)
            market = _persist_market_data(
                db=db,
                product=product,
                source=result.source,
                price=result.price,
                sold_count=result.sold_count,
                is_not_found=result.is_not_found,
                raw_payload=result.raw_payload,
                last_checked_at=result.last_checked_at,
            )
            _update_effective_state(state, market, None, ProfitabilityLabel.nieokreslony)
            db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[persist-error] ean={ean} err={exc!r}")

    outcome = "success"
    if getattr(result, "blocked", False):
        outcome = "blocked"
    elif result.is_temporary_error:
        outcome = "error"
    elif result.is_not_found:
        outcome = "no_results"
    return outcome, duration


async def main():
    args = parse_args()
    os.environ.setdefault("SCRAPER_MODE", args.mode)
    eans: List[str] = [ean.strip() for ean in (args.eans.split(",") if args.eans else DEFAULT_EANS) if ean.strip()]

    stats = {"success": 0, "no_results": 0, "blocked": 0, "error": 0}
    total_start = time.monotonic()
    for ean in eans:
        outcome, duration = await scrape_one(ean, args.mode)
        stats[outcome] = stats.get(outcome, 0) + 1
        print(f"[{args.mode}] ean={ean} outcome={outcome} duration={duration:.1f}s")

    total_duration = time.monotonic() - total_start
    total_runs = sum(stats.values())
    success_rate = (stats["success"] / total_runs * 100) if total_runs else 0
    print("\n---- Smoke summary ----")
    print(f"mode={args.mode}")
    print(f"runs={total_runs} success={stats['success']} no_results={stats['no_results']} blocked={stats['blocked']} error={stats['error']}")
    print(f"success_rate={success_rate:.1f}% total_time={total_duration:.1f}s avg_time={total_duration/total_runs:.2f}s" if total_runs else "no runs")


if __name__ == "__main__":
    asyncio.run(main())
