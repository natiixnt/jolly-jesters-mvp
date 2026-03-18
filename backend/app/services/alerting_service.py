from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
WEBHOOK_TIMEOUT = float(os.getenv("ALERT_WEBHOOK_TIMEOUT", "5"))


def send_alert(
    event: str,
    severity: str = "warning",
    details: Optional[Dict[str, Any]] = None,
    run_id: Optional[int] = None,
) -> bool:
    """Send alert via webhook. Returns True if delivered, False otherwise."""
    if not WEBHOOK_URL:
        logger.debug("ALERT skipped (no ALERT_WEBHOOK_URL): event=%s", event)
        return False

    payload = {
        "event": event,
        "severity": severity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "jolly-jesters",
        "details": details or {},
    }
    if run_id:
        payload["run_id"] = run_id

    try:
        resp = httpx.post(
            WEBHOOK_URL,
            json=payload,
            timeout=WEBHOOK_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code < 400:
            logger.info("ALERT sent: event=%s status=%s", event, resp.status_code)
            return True
        logger.warning("ALERT delivery failed: event=%s status=%s body=%s", event, resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        logger.warning("ALERT delivery error: event=%s error=%s", event, repr(exc))
        return False


def alert_stoploss(run_id: int, reason: str, details: Dict) -> bool:
    return send_alert(
        event="stoploss_triggered",
        severity="critical",
        run_id=run_id,
        details={"reason": reason, **details},
    )


def alert_high_error_rate(run_id: int, rate: float) -> bool:
    return send_alert(
        event="high_error_rate",
        severity="warning",
        run_id=run_id,
        details={"error_rate": rate},
    )


def alert_quota_exceeded(tenant_id: str, used: int, quota: int) -> bool:
    return send_alert(
        event="quota_exceeded",
        severity="warning",
        details={"tenant_id": tenant_id, "used": used, "quota": quota},
    )
