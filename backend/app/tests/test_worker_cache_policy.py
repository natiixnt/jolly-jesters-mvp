from datetime import datetime, timedelta, timezone

from app.models.product_market_data import ProductMarketData
from app.workers.tasks import _is_fresh_market_data, _should_fetch_from_scraper


def test_is_fresh_market_data_true_within_ttl():
    now = datetime.now(timezone.utc)
    market_data = ProductMarketData(last_checked_at=now - timedelta(days=1))
    assert _is_fresh_market_data(market_data, cache_days=30, now=now) is True


def test_is_fresh_market_data_false_after_ttl():
    now = datetime.now(timezone.utc)
    market_data = ProductMarketData(last_checked_at=now - timedelta(days=31))
    assert _is_fresh_market_data(market_data, cache_days=30, now=now) is False


def test_is_fresh_market_data_false_when_disabled():
    now = datetime.now(timezone.utc)
    market_data = ProductMarketData(last_checked_at=now - timedelta(days=1))
    assert _is_fresh_market_data(market_data, cache_days=0, now=now) is False


def test_not_found_fresh_market_data_skips_scraper_in_live_flow():
    now = datetime.now(timezone.utc)
    market_data = ProductMarketData(
        is_not_found=True,
        last_checked_at=now - timedelta(days=1),
    )
    assert _should_fetch_from_scraper(
        db_only_mode=False,
        market_data=market_data,
        cache_days=30,
        now=now,
    ) is False


def test_not_found_stale_market_data_triggers_rescrape_in_live_flow():
    now = datetime.now(timezone.utc)
    market_data = ProductMarketData(
        is_not_found=True,
        last_checked_at=now - timedelta(days=31),
    )
    assert _should_fetch_from_scraper(
        db_only_mode=False,
        market_data=market_data,
        cache_days=30,
        now=now,
    ) is True
