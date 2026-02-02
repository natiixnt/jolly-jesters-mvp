"""Tiny Redis-backed counters for Bright Data Browser runs.

We keep a per-day hash with success/error breakdown so health/status endpoints
can report quick win/loss ratios without Prometheus.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict

import redis

from app.core.config import settings

_METRIC_KEY = "sbr:metrics:v1"


def _redis_client():
    try:
        return redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


def _bucket(day_offset: int = 0) -> str:
    return (datetime.utcnow() - timedelta(days=day_offset)).strftime("%Y%m%d")


def record_outcome(
    outcome: str,
    duration_seconds: float,
    *,
    blocked: bool = False,
    status_code: int | None = None,
    cached: bool = False,
) -> None:
    """Increment counters for one scrape attempt."""
    client = _redis_client()
    if not client:
        return
    outcome_key = outcome or "unknown"
    key = f"{_METRIC_KEY}:{_bucket(0)}"
    pipe = client.pipeline()
    pipe.hincrby(key, "total", 1)
    pipe.hincrby(key, outcome_key, 1)
    pipe.hincrbyfloat(key, "total_duration_seconds", float(duration_seconds))
    if blocked:
        pipe.hincrby(key, "blocked", 1)
    if cached:
        pipe.hincrby(key, "cached", 1)
    if status_code:
        pipe.hincrby(key, f"status_{status_code}", 1)
    pipe.expire(key, 3 * 24 * 3600)
    pipe.execute()


def read_snapshot(days: int = 1) -> Dict[str, float]:
    """Aggregate counters from the last N days (default: today only)."""
    client = _redis_client()
    summary: Dict[str, float] = {
        "total": 0,
        "success": 0,
        "no_results": 0,
        "error": 0,
        "blocked": 0,
        "cached": 0,
        "total_duration_seconds": 0.0,
        "status_403": 0,
    }
    if not client:
        summary["warning"] = "redis_unavailable"
        summary.update(_derived_metrics(summary))
        return summary

    for day in range(max(1, days)):
        data = client.hgetall(f"{_METRIC_KEY}:{_bucket(day)}")
        if not data:
            continue
        for key, val in data.items():
            try:
                if key.startswith("status_"):
                    summary[key] = summary.get(key, 0) + int(val)
                elif key == "total_duration_seconds":
                    summary[key] += float(val)
                else:
                    summary[key] = summary.get(key, 0) + int(val)
            except Exception:
                continue

    summary.update(_derived_metrics(summary))
    return summary


def _derived_metrics(raw: Dict[str, float]) -> Dict[str, float]:
    total = max(0, int(raw.get("total") or 0))
    blocked = int(raw.get("blocked") or 0)
    success = int(raw.get("success") or 0)
    no_results = int(raw.get("no_results") or 0)
    errors = int(raw.get("error") or 0)
    status_403 = int(raw.get("status_403") or 0)
    duration = float(raw.get("total_duration_seconds") or 0.0)

    return {
        "success_rate": round(success / total, 4) if total else 0.0,
        "captcha_rate": round(blocked / total, 4) if total else 0.0,
        "http_403_rate": round(status_403 / total, 4) if total else 0.0,
        "avg_time_per_ean": round(duration / total, 3) if total else 0.0,
        "no_result_rate": round(no_results / total, 4) if total else 0.0,
        "error_rate": round(errors / total, 4) if total else 0.0,
    }


__all__ = ["record_outcome", "read_snapshot"]
