from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import analysis as analysis_api
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.analysis_run import AnalysisRun
from app.models.category import Category
from app.models.enums import AnalysisStatus
from app.services.analysis_service import process_analysis_run


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session):
    def _get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_cancel_run_endpoint_marks_canceled(client, db_session, monkeypatch):
    category = Category(
        name="CancelCat",
        profitability_multiplier=1.2,
        commission_rate=0,
        is_active=True,
    )
    db_session.add(category)
    db_session.flush()

    run = AnalysisRun(
        category_id=category.id,
        input_file_name="test.xlsx",
        status=AnalysisStatus.running,
        total_products=5,
        processed_products=1,
        mode="mixed",
        use_cloud_http=False,
        use_local_scraper=True,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr(analysis_api.celery_app.control, "revoke", lambda *_args, **_kwargs: None)

    res = client.post(f"/api/v1/analysis/{run.id}/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == AnalysisStatus.canceled.value

    refreshed = db_session.query(AnalysisRun).filter(AnalysisRun.id == run.id).first()
    assert refreshed.status == AnalysisStatus.canceled
    assert refreshed.canceled_at is not None

    res_again = client.post(f"/api/v1/analysis/{run.id}/cancel")
    assert res_again.status_code == 200


def test_process_analysis_run_skips_canceled(db_session):
    category = Category(
        name="SkipCat",
        profitability_multiplier=1.2,
        commission_rate=0,
        is_active=True,
    )
    db_session.add(category)
    db_session.flush()

    run = AnalysisRun(
        category_id=category.id,
        input_file_name="cached_db",
        status=AnalysisStatus.canceled,
        total_products=0,
        processed_products=0,
        mode="offline",
        use_cloud_http=False,
        use_local_scraper=False,
        started_at=datetime.now(timezone.utc),
        canceled_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    process_analysis_run(db_session, run.id, mode="offline")

    refreshed = db_session.query(AnalysisRun).filter(AnalysisRun.id == run.id).first()
    assert refreshed.status == AnalysisStatus.canceled
    assert refreshed.processed_products == 0
