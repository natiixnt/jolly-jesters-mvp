from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.models.enums import ScrapeStatus


@dataclass
class StopLossConfig:
    enabled: bool = True
    window_size: int = 20
    max_error_rate: float = 0.50
    max_captcha_rate: float = 0.80
    max_consecutive_errors: int = 10
    max_retry_rate: float = 0.05
    max_blocked_rate: float = 0.10
    max_cost_per_1000: float = 10.0


@dataclass
class StopLossVerdict:
    should_stop: bool
    reason: Optional[str] = None
    details: Optional[dict] = None


_ERROR_STATUSES = frozenset({
    ScrapeStatus.error,
    ScrapeStatus.network_error,
    ScrapeStatus.blocked,
})


class StopLossChecker:
    def __init__(self, config: StopLossConfig):
        self.config = config
        self.window: deque = deque(maxlen=config.window_size)
        self.consecutive_errors = 0

    def record(
        self,
        scrape_status: ScrapeStatus,
        captcha_solves: int = 0,
        retries: int = 0,
        is_blocked: bool = False,
        cost: float = 0.0,
    ) -> StopLossVerdict:
        if not self.config.enabled:
            return StopLossVerdict(should_stop=False)

        is_error = scrape_status in _ERROR_STATUSES

        self.window.append({
            "is_error": is_error,
            "has_captcha": captcha_solves > 0,
            "has_retry": retries > 0,
            "is_blocked": is_blocked or scrape_status == ScrapeStatus.blocked,
            "cost": cost,
        })

        # consecutive errors
        if is_error:
            self.consecutive_errors += 1
        else:
            self.consecutive_errors = 0

        now_iso = datetime.now(timezone.utc).isoformat()

        if self.consecutive_errors >= self.config.max_consecutive_errors:
            return StopLossVerdict(
                should_stop=True,
                reason="consecutive_errors",
                details={
                    "consecutive": self.consecutive_errors,
                    "threshold": self.config.max_consecutive_errors,
                    "timestamp": now_iso,
                },
            )

        # rate-based checks only when window is full
        if len(self.window) < self.config.window_size:
            return StopLossVerdict(should_stop=False)

        window_size = len(self.window)

        # error rate
        error_count = sum(1 for e in self.window if e["is_error"])
        error_rate = error_count / window_size
        if error_rate > self.config.max_error_rate:
            return StopLossVerdict(
                should_stop=True,
                reason="error_rate",
                details={
                    "rate": round(error_rate, 4),
                    "threshold": self.config.max_error_rate,
                    "window": window_size,
                    "timestamp": now_iso,
                },
            )

        # captcha rate
        captcha_count = sum(1 for e in self.window if e["has_captcha"])
        captcha_rate = captcha_count / window_size
        if captcha_rate > self.config.max_captcha_rate:
            return StopLossVerdict(
                should_stop=True,
                reason="captcha_rate",
                details={
                    "rate": round(captcha_rate, 4),
                    "threshold": self.config.max_captcha_rate,
                    "window": window_size,
                    "timestamp": now_iso,
                },
            )

        # retry rate
        retry_count = sum(1 for e in self.window if e["has_retry"])
        retry_rate = retry_count / window_size
        if retry_rate > self.config.max_retry_rate:
            return StopLossVerdict(
                should_stop=True,
                reason="retry_rate",
                details={
                    "rate": round(retry_rate, 4),
                    "threshold": self.config.max_retry_rate,
                    "window": window_size,
                    "timestamp": now_iso,
                },
            )

        # blocked rate
        blocked_count = sum(1 for e in self.window if e["is_blocked"])
        blocked_rate = blocked_count / window_size
        if blocked_rate > self.config.max_blocked_rate:
            return StopLossVerdict(
                should_stop=True,
                reason="blocked_rate",
                details={
                    "rate": round(blocked_rate, 4),
                    "threshold": self.config.max_blocked_rate,
                    "window": window_size,
                    "timestamp": now_iso,
                },
            )

        # cost per 1000
        total_cost = sum(e["cost"] for e in self.window)
        cost_per_1000 = (total_cost / window_size) * 1000
        if cost_per_1000 > self.config.max_cost_per_1000:
            return StopLossVerdict(
                should_stop=True,
                reason="cost_per_1000",
                details={
                    "cost_per_1000": round(cost_per_1000, 2),
                    "threshold": self.config.max_cost_per_1000,
                    "window": window_size,
                    "timestamp": now_iso,
                },
            )

        return StopLossVerdict(should_stop=False)
