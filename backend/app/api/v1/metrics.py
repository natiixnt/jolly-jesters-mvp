"""Prometheus-compatible metrics endpoint."""

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

    # -- aggregated throughput & cost across recent completed runs --
    from sqlalchemy import desc
    recent_runs = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.status == AnalysisStatus.completed)
        .order_by(desc(AnalysisRun.finished_at))
        .limit(20)
        .all()
    )

    ean_per_min_values = []
    cost_per_1000_values = []
    for r in recent_runs:
        if r.started_at and r.finished_at and r.processed_products and r.processed_products > 0:
            elapsed = (r.finished_at - r.started_at).total_seconds()
            if elapsed > 0:
                ean_per_min_values.append(r.processed_products / (elapsed / 60))

    lines.append("# HELP jj_ean_per_min_avg Average EAN/min across recent completed runs")
    lines.append("# TYPE jj_ean_per_min_avg gauge")
    avg_epm = round(sum(ean_per_min_values) / len(ean_per_min_values), 2) if ean_per_min_values else 0
    lines.append(f"jj_ean_per_min_avg {avg_epm}")

    # cost_per_1000_ean_avg - computed via analysis_service for accuracy
    from app.services import analysis_service
    for r in recent_runs:
        m = analysis_service.get_run_metrics(db, r.id)
        if m and m.cost_per_1000_ean is not None:
            cost_per_1000_values.append(m.cost_per_1000_ean)

    lines.append("# HELP jj_cost_per_1000_ean_avg Average cost per 1000 EAN across recent runs")
    lines.append("# TYPE jj_cost_per_1000_ean_avg gauge")
    avg_cost = round(sum(cost_per_1000_values) / len(cost_per_1000_values), 4) if cost_per_1000_values else 0
    lines.append(f"jj_cost_per_1000_ean_avg {avg_cost}")

    # stop-loss triggers
    stoploss_count = (
        db.query(func.count(AnalysisRun.id))
        .filter(AnalysisRun.status == AnalysisStatus.stopped)
        .scalar() or 0
    )
    lines.append("# HELP jj_stoploss_triggers_total Total runs stopped by guardrail")
    lines.append("# TYPE jj_stoploss_triggers_total counter")
    lines.append(f"jj_stoploss_triggers_total {stoploss_count}")

    # quarantined proxies
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    proxy_quarantined = (
        db.query(func.count(NetworkProxy.id))
        .filter(NetworkProxy.quarantine_until.isnot(None), NetworkProxy.quarantine_until > now)
        .scalar() or 0
    )
    lines.append("# HELP jj_proxy_quarantined Currently quarantined proxies")
    lines.append("# TYPE jj_proxy_quarantined gauge")
    lines.append(f"jj_proxy_quarantined {proxy_quarantined}")

    return "\n".join(lines) + "\n"
