"""Prometheus-compatible metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.analysis_run import AnalysisRun
from app.models.analysis_run_item import AnalysisRunItem
from app.models.enums import AnalysisStatus, ScrapeStatus
from app.models.network_proxy import NetworkProxy

router = APIRouter(tags=["metrics"])


@router.get("/prometheus", response_class=PlainTextResponse)
def prometheus_metrics(db: Session = Depends(get_db)):
    lines = []

    # analysis run counts by status
    status_counts = (
        db.query(AnalysisRun.status, func.count(AnalysisRun.id))
        .group_by(AnalysisRun.status)
        .all()
    )
    lines.append("# HELP jj_analysis_runs_total Total analysis runs by status")
    lines.append("# TYPE jj_analysis_runs_total counter")
    for status, count in status_counts:
        s = status.value if hasattr(status, "value") else str(status)
        lines.append(f'jj_analysis_runs_total{{status="{s}"}} {count}')

    # active runs
    active = (
        db.query(func.count(AnalysisRun.id))
        .filter(AnalysisRun.status.in_([AnalysisStatus.running, AnalysisStatus.pending]))
        .scalar() or 0
    )
    lines.append("# HELP jj_active_runs Currently active analysis runs")
    lines.append("# TYPE jj_active_runs gauge")
    lines.append(f"jj_active_runs {active}")

    # total EANs processed
    total_processed = db.query(func.sum(AnalysisRun.processed_products)).scalar() or 0
    lines.append("# HELP jj_eans_processed_total Total EANs processed across all runs")
    lines.append("# TYPE jj_eans_processed_total counter")
    lines.append(f"jj_eans_processed_total {total_processed}")

    # scrape status distribution (last 1000 items)
    scrape_counts = (
        db.query(AnalysisRunItem.scrape_status, func.count(AnalysisRunItem.id))
        .group_by(AnalysisRunItem.scrape_status)
        .all()
    )
    lines.append("# HELP jj_scrape_status_total Scrape results by status")
    lines.append("# TYPE jj_scrape_status_total counter")
    for status, count in scrape_counts:
        s = status.value if hasattr(status, "value") else str(status)
        lines.append(f'jj_scrape_status_total{{status="{s}"}} {count}')

    # total captcha solves
    total_captcha = db.query(func.coalesce(func.sum(AnalysisRunItem.captcha_solves), 0)).scalar() or 0
    lines.append("# HELP jj_captcha_solves_total Total CAPTCHA solves")
    lines.append("# TYPE jj_captcha_solves_total counter")
    lines.append(f"jj_captcha_solves_total {total_captcha}")

    # avg latency
    avg_lat = db.query(func.avg(AnalysisRunItem.latency_ms)).filter(AnalysisRunItem.latency_ms.isnot(None)).scalar()
    lines.append("# HELP jj_avg_latency_ms Average scrape latency in ms")
    lines.append("# TYPE jj_avg_latency_ms gauge")
    lines.append(f"jj_avg_latency_ms {round(float(avg_lat), 1) if avg_lat else 0}")

    # proxy pool health
    proxy_total = db.query(func.count(NetworkProxy.id)).scalar() or 0
    proxy_active = db.query(func.count(NetworkProxy.id)).filter(NetworkProxy.is_active.is_(True)).scalar() or 0
    lines.append("# HELP jj_proxy_total Total proxies in pool")
    lines.append("# TYPE jj_proxy_total gauge")
    lines.append(f"jj_proxy_total {proxy_total}")
    lines.append("# HELP jj_proxy_active Active proxies in pool")
    lines.append("# TYPE jj_proxy_active gauge")
    lines.append(f"jj_proxy_active {proxy_active}")

    return "\n".join(lines) + "\n"
