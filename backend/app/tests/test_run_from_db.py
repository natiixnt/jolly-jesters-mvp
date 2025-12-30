from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import analysis as analysis_api
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.category import Category
from app.models.product import Product


class _DummyResult:
    def __init__(self, task_id: str = "task-1") -> None:
        self.id = task_id


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


def _seed_category(db_session):
    category = Category(
        name="CacheCat",
        profitability_multiplier=1.2,
        commission_rate=0,
        is_active=True,
    )
    db_session.add(category)
    db_session.flush()
    return category


def test_run_from_cache_endpoint_creates_run(client, db_session, monkeypatch):
    category = _seed_category(db_session)

    product = Product(
        category_id=category.id,
        ean="9999999999999",
        name="Cached item",
        purchase_price=10,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    db_session.commit()

    monkeypatch.setattr(settings, "proxy_list_raw", "http://proxy.local")
    monkeypatch.setattr(analysis_api.run_analysis_task, "delay", lambda *_args, **_kwargs: _DummyResult())

    payload = {
        "category_id": str(category.id),
        "mode": "mixed",
        "use_cloud_http": True,
        "use_local_scraper": False,
        "cache_days": 30,
        "only_with_data": False,
    }
    res = client.post("/api/v1/analysis/run_from_cache", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "analysis_run_id" in data

    run = db_session.query(AnalysisRun).filter(AnalysisRun.id == data["analysis_run_id"]).first()
    assert run is not None
    assert run.input_file_name == "cached_db"
    assert run.input_source == "cache"
    assert run.total_products == 1

    items = (
        db_session.query(AnalysisRunItem)
        .filter(AnalysisRunItem.analysis_run_id == run.id)
        .all()
    )
    assert len(items) == 1


def test_run_from_cache_empty_returns_400(client, db_session, monkeypatch):
    category = _seed_category(db_session)
    db_session.commit()

    monkeypatch.setattr(settings, "proxy_list_raw", "http://proxy.local")
    monkeypatch.setattr(analysis_api.run_analysis_task, "delay", lambda *_args, **_kwargs: _DummyResult())

    payload = {
        "category_id": str(category.id),
        "mode": "mixed",
        "use_cloud_http": True,
        "use_local_scraper": False,
        "cache_days": 30,
        "only_with_data": False,
    }
    res = client.post("/api/v1/analysis/run_from_cache", json=payload)
    assert res.status_code == 400
