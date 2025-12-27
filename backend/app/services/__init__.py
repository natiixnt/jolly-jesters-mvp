# Intentionally avoid eager imports of heavy modules (analysis_service triggers
# Celery/scraper dependencies). Import services directly where needed.
