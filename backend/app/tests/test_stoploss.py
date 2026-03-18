"""Tests for the StopLossChecker service."""

from app.models.enums import ScrapeStatus
from app.services.stoploss_service import StopLossChecker, StopLossConfig


def test_no_stop_when_disabled():
    checker = StopLossChecker(StopLossConfig(enabled=False))
    for _ in range(100):
        v = checker.record(ScrapeStatus.error)
        assert not v.should_stop


def test_consecutive_errors_trigger():
    config = StopLossConfig(enabled=True, max_consecutive_errors=3, window_size=50)
    checker = StopLossChecker(config)
    checker.record(ScrapeStatus.error)
    checker.record(ScrapeStatus.error)
    v = checker.record(ScrapeStatus.error)
    assert v.should_stop
    assert v.reason == "consecutive_errors"


def test_consecutive_errors_reset_on_success():
    config = StopLossConfig(enabled=True, max_consecutive_errors=3, window_size=50)
    checker = StopLossChecker(config)
    checker.record(ScrapeStatus.error)
    checker.record(ScrapeStatus.error)
    checker.record(ScrapeStatus.ok)  # resets counter
    v = checker.record(ScrapeStatus.error)
    assert not v.should_stop


def test_error_rate_trigger():
    config = StopLossConfig(enabled=True, window_size=4, max_error_rate=0.5, max_consecutive_errors=100)
    checker = StopLossChecker(config)
    checker.record(ScrapeStatus.ok)
    checker.record(ScrapeStatus.error)
    checker.record(ScrapeStatus.error)
    v = checker.record(ScrapeStatus.error)  # 3/4 = 75% > 50%
    assert v.should_stop
    assert v.reason == "error_rate"


def test_captcha_rate_trigger():
    config = StopLossConfig(enabled=True, window_size=4, max_captcha_rate=0.5, max_error_rate=1.0, max_consecutive_errors=100)
    checker = StopLossChecker(config)
    checker.record(ScrapeStatus.ok, captcha_solves=1)
    checker.record(ScrapeStatus.ok, captcha_solves=1)
    checker.record(ScrapeStatus.ok, captcha_solves=1)
    v = checker.record(ScrapeStatus.ok, captcha_solves=0)  # 3/4 = 75% > 50%
    assert v.should_stop
    assert v.reason == "captcha_rate"


def test_no_trigger_before_window_full():
    config = StopLossConfig(enabled=True, window_size=10, max_error_rate=0.1, max_consecutive_errors=100)
    checker = StopLossChecker(config)
    # all errors but window not full yet (only 5 of 10)
    for _ in range(5):
        v = checker.record(ScrapeStatus.error)
    # should not trigger rate check (only consecutive could trigger)
    assert not v.should_stop


def test_healthy_run_no_stop():
    config = StopLossConfig(enabled=True, window_size=5, max_error_rate=0.5, max_captcha_rate=0.8, max_consecutive_errors=10)
    checker = StopLossChecker(config)
    for _ in range(20):
        v = checker.record(ScrapeStatus.ok, captcha_solves=0)
    assert not v.should_stop
