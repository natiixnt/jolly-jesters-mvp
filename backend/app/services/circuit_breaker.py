"""Simple circuit breaker for external service calls."""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str = "default"
    failure_threshold: int = 10
    recovery_timeout: float = 60.0  # seconds before trying again
    _failure_count: int = field(default=0, repr=False)
    _state: str = field(default=STATE_CLOSED, repr=False)
    _last_failure_time: float = field(default=0.0, repr=False)

    def record_success(self) -> None:
        if self._state == STATE_HALF_OPEN:
            logger.info("CIRCUIT_BREAKER %s recovered", self.name)
        self._failure_count = 0
        self._state = STATE_CLOSED

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            if self._state != STATE_OPEN:
                logger.warning("CIRCUIT_BREAKER %s OPEN after %d failures", self.name, self._failure_count)
            self._state = STATE_OPEN

    def is_open(self) -> bool:
        if self._state == STATE_CLOSED:
            return False
        if self._state == STATE_OPEN:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = STATE_HALF_OPEN
                logger.info("CIRCUIT_BREAKER %s half-open (trying recovery)", self.name)
                return False
            return True
        return False  # half-open: allow one attempt

    @property
    def state(self) -> str:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count
