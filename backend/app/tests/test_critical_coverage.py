"""Critical coverage tests - billing, categories, notifications, settings, audit.

Covers the five biggest test gaps for the platform.
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base

# ---------------------------------------------------------------------------
# Fixtures - in-memory SQLite
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tenant(db: Session, quota: int = 1000) -> "Tenant":
    from app.models.tenant import Tenant

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Test Tenant",
        slug="test-tenant-" + uuid.uuid4().hex[:8],
        monthly_ean_quota=quota,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def _make_category(db: Session, tenant_id=None, name: Optional[str] = None) -> "Category":
    from app.models.category import Category

    cat = Category(
        id=uuid.uuid4(),
        name=name or ("cat-" + uuid.uuid4().hex[:8]),
        profitability_multiplier=Decimal("1.5"),
        tenant_id=tenant_id,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _make_run(db: Session, tenant_id, category_id, processed: int = 10) -> "AnalysisRun":
    from app.models.analysis_run import AnalysisRun
    from app.models.enums import AnalysisStatus

    run = AnalysisRun(
        tenant_id=tenant_id,
        category_id=category_id,
        input_file_name="test.xlsx",
        status=AnalysisStatus.completed,
        processed_products=processed,
        total_products=processed,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ===========================================================================
# 1. Billing / Usage
# ===========================================================================


class TestBillingUsage:
    """Quota tracking, usage aggregation, and quota checks."""

    def test_record_run_usage_creates_record(self, db_session):
        from app.services.billing_service import record_run_usage

        tenant = _make_tenant(db_session, quota=500)
        cat = _make_category(db_session, tenant_id=tenant.id)
        run = _make_run(db_session, tenant_id=tenant.id, category_id=cat.id, processed=25)

        record = record_run_usage(db_session, run.id)

        assert record is not None
        assert record.tenant_id == tenant.id
        assert record.ean_count == 25
        assert record.period == datetime.now(timezone.utc).strftime("%Y-%m")

    def test_record_run_usage_returns_none_for_missing_run(self, db_session):
        from app.services.billing_service import record_run_usage

        result = record_run_usage(db_session, 99999)
        assert result is None

    def test_get_period_usage_aggregates(self, db_session):
        from app.models.usage_record import UsageRecord
        from app.services.billing_service import get_period_usage

        tenant = _make_tenant(db_session, quota=1000)
        period = datetime.now(timezone.utc).strftime("%Y-%m")

        # insert two usage records manually
        for ean_count in (10, 20):
            db_session.add(UsageRecord(
                tenant_id=tenant.id,
                period=period,
                ean_count=ean_count,
                captcha_count=ean_count * 2,
                estimated_cost=Decimal("0.01") * ean_count,
            ))
        db_session.commit()

        usage = get_period_usage(db_session, tenant.id, period)

        assert usage["total_ean"] == 30
        assert usage["total_captcha"] == 60
        assert usage["run_count"] == 2
        assert usage["quota"] == 1000
        assert usage["remaining"] == 970

    def test_check_quota_allowed(self, db_session):
        from app.services.billing_service import check_quota

        tenant = _make_tenant(db_session, quota=100)
        result = check_quota(db_session, tenant.id, requested_ean=50)

        assert result["allowed"] is True
        assert result["remaining"] == 100
        assert result["requested"] == 50

    def test_check_quota_denied(self, db_session):
        from app.models.usage_record import UsageRecord
        from app.services.billing_service import check_quota

        tenant = _make_tenant(db_session, quota=100)
        period = datetime.now(timezone.utc).strftime("%Y-%m")

        db_session.add(UsageRecord(
            tenant_id=tenant.id,
            period=period,
            ean_count=95,
            captcha_count=0,
            estimated_cost=Decimal("0"),
        ))
        db_session.commit()

        result = check_quota(db_session, tenant.id, requested_ean=10)
        assert result["allowed"] is False
        assert result["remaining"] == 5


# ===========================================================================
# 2. Categories CRUD
# ===========================================================================


class TestCategoriesCRUD:
    """Create, read, update, list operations on categories."""

    def test_list_categories(self, db_session):
        from app.services.categories_service import list_categories

        _make_category(db_session, name="Alpha")
        _make_category(db_session, name="Beta")

        cats = list_categories(db_session)
        names = [c.name for c in cats]

        assert "Alpha" in names
        assert "Beta" in names
        assert len(cats) >= 2

    def test_create_category(self, db_session):
        from app.schemas.category import CategoryCreate
        from app.services.categories_service import create_category

        payload = CategoryCreate(
            name="Electronics",
            profitability_multiplier=Decimal("2.0"),
        )
        cat = create_category(db_session, payload)

        assert cat.name == "Electronics"
        assert cat.profitability_multiplier == Decimal("2.0")
        assert cat.is_active is True
        assert cat.id is not None

    def test_update_category(self, db_session):
        from app.schemas.category import CategoryUpdate
        from app.services.categories_service import create_category, update_category
        from app.schemas.category import CategoryCreate

        original = create_category(
            db_session,
            CategoryCreate(name="Old Name", profitability_multiplier=Decimal("1.0")),
        )
        updated = update_category(
            db_session,
            original.id,
            CategoryUpdate(name="New Name"),
        )

        assert updated is not None
        assert updated.name == "New Name"
        # unchanged field should stay the same
        assert updated.profitability_multiplier == Decimal("1.0")

    def test_get_category_by_id(self, db_session):
        from app.services.categories_service import get_category

        cat = _make_category(db_session, name="Findme")
        result = get_category(db_session, cat.id)

        assert result is not None
        assert result.name == "Findme"

    def test_get_category_returns_none_for_missing(self, db_session):
        from app.services.categories_service import get_category

        result = get_category(db_session, uuid.uuid4())
        assert result is None


# ===========================================================================
# 3. Notification service
# ===========================================================================


class TestNotificationService:
    """Create, list, count, and mark-read for notifications."""

    def test_create_notification(self, db_session):
        from app.services.notification_service import create_notification

        tenant = _make_tenant(db_session)
        n = create_notification(
            db_session,
            tenant_id=tenant.id,
            notification_type="alert",
            title="Test Alert",
            message="Something happened",
        )

        assert n.id is not None
        assert n.title == "Test Alert"
        assert n.is_read is False

    def test_list_notifications(self, db_session):
        from app.services.notification_service import (
            create_notification,
            list_notifications,
        )

        tenant = _make_tenant(db_session)
        tid = tenant.id

        create_notification(db_session, tid, "alert", "First", "msg1")
        create_notification(db_session, tid, "system", "Second", "msg2")

        notes = list_notifications(db_session, tid)
        assert len(notes) == 2

    def test_count_unread(self, db_session):
        from app.services.notification_service import (
            count_unread,
            create_notification,
            mark_read,
        )

        tenant = _make_tenant(db_session)
        tid = tenant.id

        n1 = create_notification(db_session, tid, "alert", "A", "a")
        create_notification(db_session, tid, "alert", "B", "b")

        assert count_unread(db_session, tid) == 2

        mark_read(db_session, tid, n1.id)
        assert count_unread(db_session, tid) == 1

    def test_mark_read_changes_status(self, db_session):
        from app.services.notification_service import create_notification, mark_read

        tenant = _make_tenant(db_session)
        tid = tenant.id
        n = create_notification(db_session, tid, "alert", "Read me", "body")

        assert n.is_read is False

        result = mark_read(db_session, tid, n.id)
        assert result is True

        db_session.refresh(n)
        assert n.is_read is True
        assert n.read_at is not None

    def test_mark_read_returns_false_for_missing(self, db_session):
        from app.services.notification_service import mark_read

        tenant = _make_tenant(db_session)
        result = mark_read(db_session, tenant.id, 999999)
        assert result is False


# ===========================================================================
# 4. Settings service
# ===========================================================================


class TestSettingsService:
    """Default creation and update persistence for settings."""

    def test_get_settings_returns_defaults(self, db_session):
        from app.services.settings_service import get_settings

        s = get_settings(db_session)

        assert s is not None
        assert s.cache_ttl_days == 30
        assert s.stoploss_enabled is True

    def test_get_settings_idempotent(self, db_session):
        from app.services.settings_service import get_settings

        s1 = get_settings(db_session)
        s2 = get_settings(db_session)

        assert s1.id == s2.id

    def test_update_settings_persists(self, db_session):
        from app.services.settings_service import get_settings, update_settings

        update_settings(db_session, cache_ttl_days=7, stoploss_enabled=False)
        s = get_settings(db_session)

        assert s.cache_ttl_days == 7
        assert s.stoploss_enabled is False

    def test_update_settings_clamps_values(self, db_session):
        from app.services.settings_service import update_settings

        s = update_settings(db_session, cache_ttl_days=999)
        assert s.cache_ttl_days == 365  # capped at 365


# ===========================================================================
# 5. Audit logging
# ===========================================================================


class TestAuditLogging:
    """Audit log_event produces correct structured output."""

    def test_log_event_returns_event_dict(self):
        from app.services.audit_service import log_event

        result = log_event(
            action="login",
            user_id="user-123",
            tenant_id="tenant-456",
            ip="127.0.0.1",
            details={"browser": "Chrome"},
        )

        assert result["action"] == "login"
        assert result["user_id"] == "user-123"
        assert result["tenant_id"] == "tenant-456"
        assert result["ip"] == "127.0.0.1"
        assert result["details"]["browser"] == "Chrome"
        assert "timestamp" in result

    def test_log_event_emits_log_record(self, caplog):
        from app.services.audit_service import log_event

        with caplog.at_level(logging.INFO, logger="audit"):
            log_event(action="delete_user", user_id="u1", ip="10.0.0.1")

        assert any("AUDIT: delete_user" in msg for msg in caplog.messages)

    def test_log_event_defaults(self):
        from app.services.audit_service import log_event

        result = log_event(action="test_action")

        assert result["user_id"] is None
        assert result["tenant_id"] is None
        assert result["ip"] is None
        assert result["details"] == {}
