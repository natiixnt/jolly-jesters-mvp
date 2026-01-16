import asyncio
import json
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _add_backend_to_path() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)


_add_backend_to_path()

import httpx  # noqa: E402
from selenium.common.exceptions import TimeoutException  # noqa: E402

import main as local_scraper_main  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.analysis_run import AnalysisRun  # noqa: E402
from app.models.analysis_run_item import AnalysisRunItem  # noqa: E402
from app.models.category import Category  # noqa: E402
from app.models.enums import AnalysisItemSource, AnalysisStatus, ScrapeStatus  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.utils import allegro_scraper_http  # noqa: E402
from app.utils.allegro_scraper_http import fetch_via_http_scraper  # noqa: E402
from app.workers import tasks  # noqa: E402


def _print_result(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"{status} {name}{suffix}")


def _run_http_mock(html: str, status_code: int = 200):
    settings.proxy_list_raw = ""
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code, text=html))
    orig_async_client = allegro_scraper_http.httpx.AsyncClient

    def client_factory(**kwargs):
        return orig_async_client(transport=transport, **kwargs)

    allegro_scraper_http.httpx.AsyncClient = client_factory
    try:
        return asyncio.run(fetch_via_http_scraper("1234567890123"))
    finally:
        allegro_scraper_http.httpx.AsyncClient = orig_async_client


def _test_http_no_results() -> bool:
    payload = {"__listing_StoreState": {"items": {"elements": []}}}
    html = f'<script data-serialize-box-id="listing">{json.dumps(payload)}</script>'
    result = _run_http_mock(html)
    return result.is_not_found is True and result.is_temporary_error is False and result.blocked is False


def _test_http_blocked() -> bool:
    html = "<html><body>captcha-delivery.com</body></html>"
    result = _run_http_mock(html)
    return result.blocked is True and result.is_temporary_error is True and result.is_not_found is False


def _test_timeout_without_marker() -> bool:
    class DummyDriver:
        def __init__(self, page_source: str):
            self._page_source = page_source

        @property
        def page_source(self) -> str:
            return self._page_source

        def set_page_load_timeout(self, *args, **kwargs):
            return None

        def get(self, *args, **kwargs):
            return None

        def find_elements(self, *args, **kwargs):
            return []

        def quit(self):
            return None

    orig_create_driver = local_scraper_main._create_driver
    orig_accept_cookies = local_scraper_main._accept_cookies
    orig_wait_for_listing = local_scraper_main._wait_for_listing_data

    def _raise_timeout(_driver):
        raise TimeoutException("timeout")

    try:
        local_scraper_main._create_driver = lambda: DummyDriver("<html></html>")
        local_scraper_main._accept_cookies = lambda _driver: None
        local_scraper_main._wait_for_listing_data = _raise_timeout
        result = local_scraper_main.scrape_single_ean("1234567890123")
    finally:
        local_scraper_main._create_driver = orig_create_driver
        local_scraper_main._accept_cookies = orig_accept_cookies
        local_scraper_main._wait_for_listing_data = orig_wait_for_listing

    return result.get("error") == "timeout" and result.get("blocked") is False and result.get("not_found") is False


def _test_finalize_not_in_progress() -> bool:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    db = SessionLocal()
    category = Category(
        name="ManualTest",
        profitability_multiplier=0.1,
        commission_rate=0,
        is_active=True,
    )
    db.add(category)
    db.commit()
    db.refresh(category)

    product = Product(
        category_id=category.id,
        ean="1234567890123",
        name="Test",
        purchase_price=1,
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    run = AnalysisRun(
        category_id=category.id,
        input_file_name="manual.xlsx",
        input_source="manual",
        total_products=1,
        processed_products=0,
        status=AnalysisStatus.running,
        mode="mixed",
        use_cloud_http=True,
        use_local_scraper=True,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    item = AnalysisRunItem(
        analysis_run_id=run.id,
        product_id=product.id,
        row_number=1,
        ean=product.ean,
        input_name="Test",
        source=AnalysisItemSource.scraping,
        scrape_status=ScrapeStatus.pending,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    item_id = item.id
    run_id = run.id
    db.close()

    orig_session_local = tasks.SessionLocal
    orig_fetch = tasks.fetch_via_local_scraper

    async def _boom(_ean: str):
        raise RuntimeError("boom")

    tasks.SessionLocal = SessionLocal
    tasks.fetch_via_local_scraper = _boom
    try:
        tasks.scrape_one_local.run("1234567890123", item_id, None)
    finally:
        tasks.SessionLocal = orig_session_local
        tasks.fetch_via_local_scraper = orig_fetch

    db = SessionLocal()
    refreshed_item = db.query(AnalysisRunItem).filter(AnalysisRunItem.id == item_id).first()
    refreshed_run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    db.close()

    if not refreshed_item or not refreshed_run:
        return False

    if refreshed_item.scrape_status == ScrapeStatus.in_progress:
        return False

    return refreshed_run.processed_products == 1


def main() -> int:
    failures = 0

    ok = _test_http_no_results()
    _print_result("http 200 + empty StoreState -> NOT_FOUND", ok)
    failures += 0 if ok else 1

    ok = _test_http_blocked()
    _print_result("http 200 + captcha/bot wall -> BLOCKED", ok)
    failures += 0 if ok else 1

    ok = _test_timeout_without_marker()
    _print_result("timeout without marker -> RETRYABLE", ok)
    failures += 0 if ok else 1

    ok = _test_finalize_not_in_progress()
    _print_result("finalization clears IN_PROGRESS", ok)
    failures += 0 if ok else 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
