"""Integration tests for new platform features.

Covers: stop-loss new thresholds, concurrency limits, proxy pool healthcheck,
cost formula in run metrics, and API key auto-expiration.
"""

import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import (
    AnalysisItemSource,
    AnalysisStatus,
    ScrapeStatus,
)
from app.models.network_proxy import NetworkProxy
from app.services.stoploss_service import StopLossChecker, StopLossConfig


# ---------------------------------------------------------------------------
# Fixtures - in-memory SQLite for DB-backed tests
# ---------------------------------------------------------------------------

# Register SQLite UUID compiler once at module level
_uuid_compiler_registered = False


def _ensure_uuid_compiler():
    global _uuid_compiler_registered
    if _uuid_compiler_registered:
        return
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy.ext.compiler import compiles

    @compiles(PG_UUID, "sqlite")
    def compile_uuid_sqlite(type_, compiler, **kw):
        return "CHAR(36)"

    _uuid_compiler_registered = True


@pytest.fixture()
def db_session():
    """Create an in-memory SQLite database and yield a session."""
    _ensure_uuid_compiler()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ===========================================================================
# 1. Stop-loss - new thresholds (retry_rate, blocked_rate, cost_per_1000)
# ===========================================================================

class TestStopLossNewThresholds:
    """Verify that the three new stop-loss thresholds trigger correctly."""

    def test_retry_rate_triggers_stop(self):
        config = StopLossConfig(
            enabled=True,
            window_size=4,
            max_retry_rate=0.25,
            max_error_rate=1.0,
            max_captcha_rate=1.0,
            max_blocked_rate=1.0,
            max_cost_per_1000=999.0,
            max_consecutive_errors=100,
        )
        checker = StopLossChecker(config)
        # 2 out of 4 have retries -> 50% > 25% threshold
        checker.record(ScrapeStatus.ok, retries=1)
        checker.record(ScrapeStatus.ok, retries=0)
        checker.record(ScrapeStatus.ok, retries=1)
        v = checker.record(ScrapeStatus.ok, retries=0)
        assert v.should_stop
        assert v.reason == "retry_rate"
        assert "timestamp" in v.details

    def test_blocked_rate_triggers_stop(self):
        config = StopLossConfig(
            enabled=True,
            window_size=4,
            max_blocked_rate=0.20,
            max_error_rate=1.0,
            max_captcha_rate=1.0,
            max_retry_rate=1.0,
            max_cost_per_1000=999.0,
            max_consecutive_errors=100,
        )
        checker = StopLossChecker(config)
        # 2 out of 4 blocked -> 50% > 20% threshold
        checker.record(ScrapeStatus.ok, is_blocked=True)
        checker.record(ScrapeStatus.ok, is_blocked=False)
        checker.record(ScrapeStatus.ok, is_blocked=True)
        v = checker.record(ScrapeStatus.ok, is_blocked=False)
        assert v.should_stop
        assert v.reason == "blocked_rate"
        assert "timestamp" in v.details

    def test_cost_per_1000_triggers_stop(self):
        config = StopLossConfig(
            enabled=True,
            window_size=4,
            max_cost_per_1000=5.0,
            max_error_rate=1.0,
            max_captcha_rate=1.0,
            max_retry_rate=1.0,
            max_blocked_rate=1.0,
            max_consecutive_errors=100,
        )
        checker = StopLossChecker(config)
        # Each costs 0.05 -> total 0.20 over 4 -> (0.20/4)*1000 = 50.0 > 5.0
        checker.record(ScrapeStatus.ok, cost=0.05)
        checker.record(ScrapeStatus.ok, cost=0.05)
        checker.record(ScrapeStatus.ok, cost=0.05)
        v = checker.record(ScrapeStatus.ok, cost=0.05)
        assert v.should_stop
        assert v.reason == "cost_per_1000"
        assert "timestamp" in v.details
        assert v.details["cost_per_1000"] == 50.0  # (0.20/4)*1000

    def test_timestamp_present_in_consecutive_errors(self):
        config = StopLossConfig(enabled=True, max_consecutive_errors=2, window_size=50)
        checker = StopLossChecker(config)
        checker.record(ScrapeStatus.error)
        v = checker.record(ScrapeStatus.error)
        assert v.should_stop
        assert v.reason == "consecutive_errors"
        assert "timestamp" in v.details
        # Verify it parses as ISO format
        ts = v.details["timestamp"]
        datetime.fromisoformat(ts)

    def test_under_all_thresholds_no_stop(self):
        config = StopLossConfig(
            enabled=True,
            window_size=4,
            max_retry_rate=0.50,
            max_blocked_rate=0.50,
            max_cost_per_1000=100.0,
            max_error_rate=1.0,
            max_captcha_rate=1.0,
            max_consecutive_errors=100,
        )
        checker = StopLossChecker(config)
        checker.record(ScrapeStatus.ok, retries=0, is_blocked=False, cost=0.001)
        checker.record(ScrapeStatus.ok, retries=1, is_blocked=False, cost=0.001)
        checker.record(ScrapeStatus.ok, retries=0, is_blocked=False, cost=0.001)
        v = checker.record(ScrapeStatus.ok, retries=0, is_blocked=False, cost=0.001)
        assert not v.should_stop


# ===========================================================================
# 2. Concurrency limits
# ===========================================================================

class TestConcurrencyLimits:
    """Verify _check_concurrent_limit rejects when limits are reached."""

    def _make_mock_run(self, user_id=None):
        run = MagicMock()
        run.user_id = user_id
        return run

    @patch("app.services.analysis_service.list_active_runs")
    @patch("app.core.config.get_settings")
    def test_rejects_when_global_limit_reached(self, mock_settings, mock_list_active):
        from fastapi import HTTPException
        from app.api.v1.analysis import _check_concurrent_limit

        mock_settings.return_value = MagicMock(
            concurrency_global_max=2,
            concurrency_per_user=5,
        )
        mock_list_active.return_value = [
            self._make_mock_run(),
            self._make_mock_run(),
        ]

        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _check_concurrent_limit(db, current_user=None)
        assert exc_info.value.status_code == 429
        assert "Globalny limit" in exc_info.value.detail

    @patch("app.services.analysis_service.list_active_runs")
    @patch("app.core.config.get_settings")
    def test_rejects_when_per_user_limit_reached(self, mock_settings, mock_list_active):
        from fastapi import HTTPException
        from app.api.v1.analysis import _check_concurrent_limit

        user_id = str(uuid.uuid4())
        mock_settings.return_value = MagicMock(
            concurrency_global_max=100,
            concurrency_per_user=1,
        )
        mock_list_active.return_value = [
            self._make_mock_run(user_id=user_id),
        ]

        db = MagicMock()
        current_user = MagicMock()
        current_user.user_id = user_id

        with pytest.raises(HTTPException) as exc_info:
            _check_concurrent_limit(db, current_user=current_user)
        assert exc_info.value.status_code == 429
        assert "uzytkownika" in exc_info.value.detail

    @patch("app.services.analysis_service.list_active_runs")
    @patch("app.core.config.get_settings")
    def test_allows_when_under_limits(self, mock_settings, mock_list_active):
        from app.api.v1.analysis import _check_concurrent_limit

        mock_settings.return_value = MagicMock(
            concurrency_global_max=10,
            concurrency_per_user=5,
        )
        mock_list_active.return_value = [
            self._make_mock_run(user_id="other-user"),
        ]

        db = MagicMock()
        current_user = MagicMock()
        current_user.user_id = str(uuid.uuid4())

        # Should not raise
        _check_concurrent_limit(db, current_user=current_user)


# ===========================================================================
# 3. Proxy pool healthcheck
# ===========================================================================

class TestProxyPoolHealthcheck:
    """Verify run_healthcheck recovers expired quarantines.

    SQLite stores naive datetimes, so we use naive timestamps and patch
    datetime.now inside the service to return a naive UTC value.
    """

    def _now_naive(self):
        return datetime.utcnow()

    def test_recovers_expired_quarantine(self, db_session):
        from app.services.proxy_pool_service import run_healthcheck, proxy_url_hash

        now = self._now_naive()
        proxy = NetworkProxy(
            url="http://expired-proxy.test:8080",
            url_hash=proxy_url_hash("http://expired-proxy.test:8080"),
            is_active=True,
            quarantine_until=now - timedelta(hours=1),
            quarantine_reason="test quarantine",
        )
        db_session.add(proxy)
        db_session.commit()

        with patch("app.services.proxy_pool_service.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = run_healthcheck(db_session)

        assert result["recovered"] == 1
        assert result["checked"] == 1

        db_session.refresh(proxy)
        assert proxy.quarantine_until is None
        assert proxy.quarantine_reason is None

    def test_does_not_recover_active_quarantine(self, db_session):
        from app.services.proxy_pool_service import run_healthcheck, proxy_url_hash

        now = self._now_naive()
        proxy = NetworkProxy(
            url="http://still-quarantined.test:8080",
            url_hash=proxy_url_hash("http://still-quarantined.test:8080"),
            is_active=True,
            quarantine_until=now + timedelta(hours=2),
            quarantine_reason="still in quarantine",
        )
        db_session.add(proxy)
        db_session.commit()

        with patch("app.services.proxy_pool_service.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = run_healthcheck(db_session)

        assert result["recovered"] == 0
        assert result["checked"] == 1

        db_session.refresh(proxy)
        assert proxy.quarantine_until is not None
        assert proxy.quarantine_reason == "still in quarantine"

    def test_mixed_proxies_recovery(self, db_session):
        from app.services.proxy_pool_service import run_healthcheck, proxy_url_hash

        now = self._now_naive()
        expired = NetworkProxy(
            url="http://expired.test:8080",
            url_hash=proxy_url_hash("http://expired.test:8080"),
            is_active=True,
            quarantine_until=now - timedelta(minutes=10),
            quarantine_reason="expired",
        )
        still_active = NetworkProxy(
            url="http://active-q.test:8080",
            url_hash=proxy_url_hash("http://active-q.test:8080"),
            is_active=True,
            quarantine_until=now + timedelta(hours=5),
            quarantine_reason="still active",
        )
        healthy = NetworkProxy(
            url="http://healthy.test:8080",
            url_hash=proxy_url_hash("http://healthy.test:8080"),
            is_active=True,
            quarantine_until=None,
        )
        db_session.add_all([expired, still_active, healthy])
        db_session.commit()

        with patch("app.services.proxy_pool_service.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = run_healthcheck(db_session)

        assert result["checked"] == 3
        assert result["recovered"] == 1

        db_session.refresh(expired)
        assert expired.quarantine_until is None

        db_session.refresh(still_active)
        assert still_active.quarantine_until is not None


# ===========================================================================
# 4. Cost formula in get_run_metrics
# ===========================================================================

class TestCostFormula:
    """Verify cost calculation uses both network and access verification costs.

    get_run_metrics imports get_settings locally, so we patch it at the
    point where the module resolves the import.
    """

    def _mock_settings(self):
        return MagicMock(
            cost_rate_network_per_gb=10.0,
            cost_rate_access_verification=5.0,
        )

    def test_cost_uses_network_and_captcha(self):
        from app.services.analysis_service import get_run_metrics

        # Build a mock run with items
        mock_run = MagicMock()
        mock_run.id = 1
        mock_run.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        mock_run.finished_at = datetime.now(timezone.utc)

        # Create 10 completed items, 5 with captcha solves
        items = []
        for i in range(10):
            item = MagicMock()
            item.scrape_status = ScrapeStatus.ok
            item.latency_ms = 200
            item.captcha_solves = 1 if i < 5 else 0
            item.retries = 0
            items.append(item)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_run
        db.query.return_value.filter.return_value.all.return_value = items

        with patch("app.core.config.get_settings", return_value=self._mock_settings()):
            metrics = get_run_metrics(db, run_id=1)

        assert metrics is not None

        # 10 processed items, 5 captcha solves
        # captcha_cost = (5 / 1000) * 5.0 = 0.025
        # gb_transfer = (10 * 50 / 1024 / 1024) ~ 0.000477 GB
        # network_cost = 0.000477 * 10.0 ~ 0.00477
        # total_cost ~ 0.02977
        # cost_per_1000 = (0.02977 / 10) * 1000 ~ 2.977
        assert metrics.cost_per_1000_ean is not None
        assert metrics.cost_per_1000_ean > 0

        # Verify captcha component contributes
        assert metrics.total_captcha_solves == 5

    def test_success_rate_calculated(self):
        from app.services.analysis_service import get_run_metrics

        mock_run = MagicMock()
        mock_run.id = 2
        mock_run.started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        mock_run.finished_at = datetime.now(timezone.utc)

        items = []
        for i in range(10):
            item = MagicMock()
            item.scrape_status = ScrapeStatus.ok if i < 7 else ScrapeStatus.error
            item.latency_ms = 100
            item.captcha_solves = 0
            item.retries = 0
            items.append(item)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_run
        db.query.return_value.filter.return_value.all.return_value = items

        with patch("app.core.config.get_settings", return_value=self._mock_settings()):
            metrics = get_run_metrics(db, run_id=2)

        assert metrics is not None
        # 7 out of 10 completed
        assert metrics.success_rate == 0.7
        assert metrics.completed_items == 7
        assert metrics.failed_items == 3


# ===========================================================================
# 5. API key auto-expiration
# ===========================================================================

class TestApiKeyAutoExpiration:
    """Verify validate_api_key deactivates expired keys."""

    def _make_tenant(self, db_session, suffix="1"):
        from app.models.tenant import Tenant
        tid = uuid.uuid4()
        tenant = Tenant(
            id=tid,
            name="test-tenant-" + suffix,
            slug="test-tenant-" + suffix,
        )
        db_session.add(tenant)
        db_session.commit()
        return tenant

    def _create_api_key_direct(self, db_session, tenant_id, name, expires_at=None):
        """Create an API key directly via the model to avoid SQLite UUID issues
        with the service layer passing string tenant_id to a UUID column."""
        import hashlib
        import secrets
        from app.models.api_key import APIKey

        raw_key = "jj_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:11]

        record = APIKey(
            tenant_id=tenant_id,
            user_id=None,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            expires_at=expires_at,
        )
        db_session.add(record)
        db_session.commit()
        db_session.refresh(record)
        return record, raw_key

    def test_expired_key_is_deactivated(self, db_session):
        from app.services.api_key_service import validate_api_key

        tenant = self._make_tenant(db_session, "exp")

        # Use naive datetime - SQLite strips timezone info.
        # Patch datetime.now in the service to also return naive.
        now_naive = datetime.utcnow()

        record, raw_key = self._create_api_key_direct(
            db_session,
            tenant_id=tenant.id,
            name="expired-key",
            expires_at=now_naive - timedelta(hours=1),
        )
        assert record.is_active is True

        with patch("app.services.api_key_service.datetime") as mock_dt:
            mock_dt.now.return_value = now_naive
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = validate_api_key(db_session, raw_key)

        assert result is None

        # Verify it was deactivated in DB
        db_session.refresh(record)
        assert record.is_active is False

    def test_valid_key_remains_active(self, db_session):
        from app.services.api_key_service import validate_api_key

        tenant = self._make_tenant(db_session, "valid")

        now_naive = datetime.utcnow()

        record, raw_key = self._create_api_key_direct(
            db_session,
            tenant_id=tenant.id,
            name="valid-key",
            expires_at=now_naive + timedelta(days=30),
        )

        with patch("app.services.api_key_service.datetime") as mock_dt:
            mock_dt.now.return_value = now_naive
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = validate_api_key(db_session, raw_key)

        assert result is not None
        assert result.id == record.id
        assert result.is_active is True
        assert result.last_used_at is not None

    def test_key_without_expiry_stays_valid(self, db_session):
        from app.services.api_key_service import validate_api_key

        tenant = self._make_tenant(db_session, "noexp")

        record, raw_key = self._create_api_key_direct(
            db_session,
            tenant_id=tenant.id,
            name="no-expiry-key",
            expires_at=None,
        )

        result = validate_api_key(db_session, raw_key)
        assert result is not None
        assert result.is_active is True


# ===========================================================================
# 6. API key scopes
# ===========================================================================

class TestApiKeyScopes:
    """Verify scope-based access control on API keys."""

    def _make_tenant(self, db_session, suffix="scope"):
        from app.models.tenant import Tenant
        tid = uuid.uuid4()
        tenant = Tenant(id=tid, name="test-tenant-" + suffix, slug="test-tenant-" + suffix)
        db_session.add(tenant)
        db_session.commit()
        return tenant

    def _create_api_key_direct(self, db_session, tenant_id, name, scopes=None):
        import hashlib
        import json
        import secrets
        from app.models.api_key import APIKey

        raw_key = "jj_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:11]

        record = APIKey(
            tenant_id=tenant_id,
            user_id=None,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            scopes=json.dumps(scopes) if scopes else '["read"]',
        )
        db_session.add(record)
        db_session.commit()
        db_session.refresh(record)
        return record, raw_key

    def test_key_with_read_scope_passes_read_check(self, db_session):
        from app.services.api_key_service import validate_api_key
        tenant = self._make_tenant(db_session, "s1")
        _record, raw_key = self._create_api_key_direct(
            db_session, tenant.id, "read-only", scopes=["read"],
        )
        result = validate_api_key(db_session, raw_key, required_scope="read")
        assert result is not None

    def test_key_with_read_scope_fails_write_check(self, db_session):
        from app.services.api_key_service import validate_api_key
        tenant = self._make_tenant(db_session, "s2")
        _record, raw_key = self._create_api_key_direct(
            db_session, tenant.id, "read-only", scopes=["read"],
        )
        result = validate_api_key(db_session, raw_key, required_scope="write")
        assert result is None

    def test_full_access_key_passes_admin_check(self, db_session):
        from app.services.api_key_service import validate_api_key
        tenant = self._make_tenant(db_session, "s3")
        _record, raw_key = self._create_api_key_direct(
            db_session, tenant.id, "full", scopes=["read", "write", "admin"],
        )
        result = validate_api_key(db_session, raw_key, required_scope="admin")
        assert result is not None

    def test_default_scopes_are_read_only(self, db_session):
        from app.models.api_key import APIKey, SCOPE_READ_ONLY
        tenant = self._make_tenant(db_session, "s4")
        _record, _raw = self._create_api_key_direct(
            db_session, tenant.id, "default",
        )
        assert _record.get_scopes() == SCOPE_READ_ONLY

    def test_validate_scopes_rejects_invalid(self):
        from app.services.api_key_service import validate_scopes
        with pytest.raises(ValueError, match="Invalid scopes"):
            validate_scopes(["read", "delete_everything"])


# ===========================================================================
# 7. API key rate limiting
# ===========================================================================

class TestApiKeyRateLimiting:
    """Verify per-key rate limiting."""

    def test_allows_requests_under_limit(self):
        from app.services.api_key_service import check_api_key_rate, _api_key_usage
        test_hash = "test_hash_under_limit"
        _api_key_usage.pop(test_hash, None)

        for _ in range(5):
            assert check_api_key_rate(test_hash, max_per_minute=10) is True

    def test_blocks_requests_over_limit(self):
        from app.services.api_key_service import check_api_key_rate, _api_key_usage
        test_hash = "test_hash_over_limit"
        _api_key_usage.pop(test_hash, None)

        for _ in range(3):
            assert check_api_key_rate(test_hash, max_per_minute=3) is True
        # Fourth request should be blocked
        assert check_api_key_rate(test_hash, max_per_minute=3) is False

    def test_old_entries_expire(self):
        import time as _time
        from app.services.api_key_service import check_api_key_rate, _api_key_usage
        test_hash = "test_hash_expire"
        # Manually insert old timestamps
        _api_key_usage[test_hash] = [_time.time() - 120] * 5  # 2 minutes ago
        # Should be allowed since old entries are cleaned
        assert check_api_key_rate(test_hash, max_per_minute=3) is True


# ===========================================================================
# 8. Token security - issuer/audience claims and refresh
# ===========================================================================

class TestTokenSecurity:
    """Verify token iss/aud claims and refresh mechanism."""

    def _make_mock_user(self):
        user = MagicMock()
        user.id = uuid.uuid4()
        user.tenant_id = uuid.uuid4()
        user.is_active = True
        return user

    def test_token_contains_iss_and_aud(self):
        from app.services.auth_service import issue_token, TOKEN_ISSUER, TOKEN_AUDIENCE
        import base64 as b64

        user = self._make_mock_user()
        token = issue_token(user)
        encoded, _sig = token.rsplit(".", 1)
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        payload = b64.urlsafe_b64decode(encoded).decode()
        parts = payload.split(":")
        assert len(parts) == 6
        assert parts[4] == TOKEN_ISSUER
        assert parts[5] == TOKEN_AUDIENCE

    def test_validate_token_accepts_valid(self):
        from app.services.auth_service import issue_token, validate_token

        user = self._make_mock_user()
        token = issue_token(user)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = user

        result = validate_token(db, token)
        assert result is not None

    def test_validate_token_rejects_tampered(self):
        from app.services.auth_service import issue_token, validate_token

        user = self._make_mock_user()
        token = issue_token(user)
        # Tamper with signature
        tampered = token[:-4] + "XXXX"

        db = MagicMock()
        result = validate_token(db, tampered)
        assert result is None

    def test_refresh_token_too_early(self):
        from app.services.auth_service import issue_token, refresh_token

        user = self._make_mock_user()
        token = issue_token(user)

        db = MagicMock()
        # Fresh token should not be refreshable
        result = refresh_token(db, token)
        assert result is None

    def test_refresh_token_in_window(self):
        from app.services.auth_service import (
            issue_token, refresh_token,
            TOKEN_TTL_HOURS, TOKEN_REFRESH_WINDOW_RATIO,
        )
        import base64 as b64

        user = self._make_mock_user()
        # Create a token that appears old (within refresh window)
        ttl_seconds = TOKEN_TTL_HOURS * 3600
        # Set iat to put us at 80% of TTL (inside 25% refresh window)
        old_iat = int(time.time() - ttl_seconds * 0.80)

        from app.services.auth_service import JWT_SECRET, TOKEN_ISSUER, TOKEN_AUDIENCE
        jti = "deadbeef01234567"
        payload = f"{user.id}:{user.tenant_id}:{old_iat}:{jti}:{TOKEN_ISSUER}:{TOKEN_AUDIENCE}"
        encoded = b64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        sig = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        old_token = f"{encoded}.{sig}"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = user

        new_token = refresh_token(db, old_token)
        assert new_token is not None
        assert new_token != old_token
