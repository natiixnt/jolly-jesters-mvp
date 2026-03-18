"""Tests for circuit breaker."""

from app.services.circuit_breaker import CircuitBreaker


def test_stays_closed_on_success():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_success()
    cb.record_success()
    assert not cb.is_open()
    assert cb.state == "closed"


def test_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open()
    cb.record_failure()
    assert cb.is_open()
    assert cb.state == "open"


def test_success_resets_failures():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert not cb.is_open()


def test_half_open_after_recovery_timeout():
    import time
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    assert cb.is_open()
    time.sleep(0.15)
    assert not cb.is_open()  # half-open
    assert cb.state == "half_open"


def test_half_open_closes_on_success():
    import time
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    time.sleep(0.15)
    cb.is_open()  # triggers half-open
    cb.record_success()
    assert cb.state == "closed"
