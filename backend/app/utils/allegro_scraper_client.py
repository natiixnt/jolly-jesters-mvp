from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.services.schemas import AllegroResult

logger = logging.getLogger(__name__)


def _scraper_base_url() -> str:
    return os.getenv("ALLEGRO_SCRAPER_URL", settings.allegro_scraper_url).rstrip("/")


def _poll_interval() -> float:
    try:
        return max(0.1, float(os.getenv("ALLEGRO_SCRAPER_POLL_INTERVAL", settings.allegro_scraper_poll_interval)))
    except Exception:
        return settings.allegro_scraper_poll_interval


def _request_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("ALLEGRO_SCRAPER_TIMEOUT_SECONDS", settings.allegro_scraper_timeout_seconds)))
    except Exception:
        return settings.allegro_scraper_timeout_seconds


def _http_client() -> httpx.AsyncClient:
    timeout = _request_timeout_seconds()
    return httpx.AsyncClient(
        base_url=_scraper_base_url(),
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
    )


def _derive_price(result: dict) -> Optional[Decimal]:
    products = result.get("products") or []
    prices = []
    for product in products:
        price = (product or {}).get("price") or {}
        amount = price.get("amount")
        if amount is None:
            continue
        try:
            prices.append(Decimal(str(amount)))
        except Exception:
            continue
    if not prices:
        return None
    return min(prices)


def _derive_sold_count(result: dict) -> Optional[int]:
    products = result.get("products") or []
    counts = []
    for product in products:
        val = (product or {}).get("recentSalesCount")
        if val is None:
            continue
        try:
            counts.append(int(val))
        except Exception:
            continue
    if not counts:
        return None
    return max(counts)


def _to_result(payload: dict) -> AllegroResult:
    status = payload.get("status") or "unknown"
    is_not_found = status == "no_results"
    price = _derive_price(payload)
    sold_count = _derive_sold_count(payload)
    scraped_at_raw = payload.get("scrapedAt")
    scraped_at = None
    if scraped_at_raw:
        try:
            scraped_at = datetime.fromisoformat(scraped_at_raw.replace("Z", "+00:00"))
        except Exception:
            scraped_at = None
    return AllegroResult(
        ean=payload.get("ean") or "",
        status=status,
        total_offer_count=payload.get("totalOfferCount"),
        products=payload.get("products") or [],
        price=price,
        sold_count=sold_count,
        is_not_found=is_not_found or (not payload.get("products")),
        is_temporary_error=False,
        raw_payload=payload,
        source="allegro_scraper",
        scraped_at=scraped_at,
        duration_ms=payload.get("durationMs"),
        captcha_solves=payload.get("captchaSolves"),
        error=None,
    )


async def fetch_via_allegro_scraper(ean: str) -> AllegroResult:
    """
    Single entrypoint used across the backend to talk to the allegro.pl-scraper-main
    service. It creates a task, polls until completion and normalises the payload.
    """
    async with _http_client() as client:
        try:
            create = await client.post("/createTask", json={"ean": ean})
        except Exception as exc:
            logger.exception("SCRAPER_HTTP_ERROR createTask ean=%s", ean)
            return AllegroResult(
                ean=ean,
                status="error",
                total_offer_count=None,
                products=[],
                price=None,
                sold_count=None,
                is_not_found=False,
                is_temporary_error=True,
                raw_payload={"error": "create_failed", "details": repr(exc)},
                error="create_failed",
                source="allegro_scraper",
            )

        if create.status_code != 201:
            detail = None
            try:
                detail = create.json()
            except Exception:
                detail = {"body": create.text}
            logger.warning("SCRAPER_CREATE_FAILED status=%s body=%s", create.status_code, detail)
            return AllegroResult(
                ean=ean,
                status="error",
                total_offer_count=None,
                products=[],
                price=None,
                sold_count=None,
                is_not_found=False,
                is_temporary_error=True,
                raw_payload={"error": "create_failed", "status_code": create.status_code, "body": detail},
                error="create_failed",
                source="allegro_scraper",
            )

        task_id = (create.json() or {}).get("taskId")
        if not task_id:
            return AllegroResult(
                ean=ean,
                status="error",
                total_offer_count=None,
                products=[],
                price=None,
                sold_count=None,
                is_not_found=False,
                is_temporary_error=True,
                raw_payload={"error": "missing_task_id"},
                error="missing_task_id",
                source="allegro_scraper",
            )

        deadline = time.time() + _request_timeout_seconds()
        poll = _poll_interval()

        while True:
            if time.time() > deadline:
                logger.warning("SCRAPER_TIMEOUT ean=%s task=%s", ean, task_id)
                return AllegroResult(
                    ean=ean,
                    status="timeout",
                    total_offer_count=None,
                    products=[],
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={"error": "timeout", "task_id": task_id},
                    error="timeout",
                    source="allegro_scraper",
                )

            try:
                resp = await client.get(f"/getTaskResult/{task_id}")
            except Exception as exc:
                logger.exception("SCRAPER_HTTP_ERROR getTaskResult ean=%s task=%s", ean, task_id)
                return AllegroResult(
                    ean=ean,
                    status="error",
                    total_offer_count=None,
                    products=[],
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={"error": "poll_failed", "details": repr(exc), "task_id": task_id},
                    error="poll_failed",
                    source="allegro_scraper",
                )

            if resp.status_code == 404:
                return AllegroResult(
                    ean=ean,
                    status="error",
                    total_offer_count=None,
                    products=[],
                    price=None,
                    sold_count=None,
                    is_not_found=False,
                    is_temporary_error=True,
                    raw_payload={"error": "task_not_found", "task_id": task_id},
                    error="task_not_found",
                    source="allegro_scraper",
                )

            payload: Dict[str, Any] = resp.json() or {}
            status = (payload.get("status") or "").lower()
            if status in {"pending", "processing"}:
                await asyncio.sleep(poll)
                continue

            if status == "completed":
                result = payload.get("result") or {}
                return _to_result({**result, "ean": ean})

            error = payload.get("error") or "scraper_failed"
            return AllegroResult(
                ean=ean,
                status=status or "error",
                total_offer_count=None,
                products=[],
                price=None,
                sold_count=None,
                is_not_found=False,
                is_temporary_error=False,
                raw_payload=payload,
                error=str(error),
                source="allegro_scraper",
            )


def check_scraper_health(timeout_seconds: float = 2.0) -> dict:
    """
    Lightweight sync health probe used by /health and /api/v1/status.
    """
    try:
        with httpx.Client(base_url=_scraper_base_url(), timeout=timeout_seconds) as client:
            resp = client.get("/health")
            if resp.status_code < 400:
                body = {}
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                poll_ms = body.get("pollInterval")
                try:
                    poll_val = float(poll_ms) / 1000 if poll_ms is not None else None
                except Exception:
                    poll_val = None
                details = {
                    "worker_count": body.get("workerCount"),
                    "concurrency_per_worker": body.get("concurrencyPerWorker"),
                    "max_task_retries": body.get("maxTaskRetries"),
                    "poll_interval": poll_val,
                    "timeout_seconds": body.get("timeoutSeconds"),
                }
                return {"status": "ok", "details": details}
            return {"status": "degraded", "status_code": resp.status_code}
    except Exception as exc:
        return {"status": "error", "error": repr(exc)}
