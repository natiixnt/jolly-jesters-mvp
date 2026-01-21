from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.analysis_run import AnalysisRun
from app.models.category import Category
from app.models.enums import AnalysisStatus


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


def test_active_runs_endpoint_returns_only_running(client, db_session):
    category = Category(
        name="ActiveCat",
        profitability_multiplier=1.2,
        commission_rate=0,
        is_active=True,
    )
    db_session.add(category)
    db_session.flush()

    run_pending = AnalysisRun(
        category_id=category.id,
        input_file_name="test.xlsx",
        status=AnalysisStatus.pending,
        total_products=1,
        processed_products=0,
        mode="mixed",
        use_cloud_http=False,
        use_local_scraper=True,
        started_at=datetime.now(timezone.utc),
    )
    run_done = AnalysisRun(
        category_id=category.id,
        input_file_name="test.xlsx",
        status=AnalysisStatus.completed,
        total_products=1,
        processed_products=1,
        mode="mixed",
        use_cloud_http=False,
        use_local_scraper=True,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add_all([run_pending, run_done])
    db_session.commit()

    res = client.get("/api/v1/analysis/active")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
    runs = data.get("runs", [])
    assert isinstance(runs, list)
    statuses = {item["status"] for item in runs}
    assert AnalysisStatus.completed.value not in statuses
    assert AnalysisStatus.pending.value in statuses
