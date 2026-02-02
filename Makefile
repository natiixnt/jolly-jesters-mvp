PYTHON ?= python3
PYTHONPATH := backend

.PHONY: test test-bd up down logs migrate smoke-brightdata smoke-legacy smoke

test:
	LOCAL_SCRAPER_SKIP_HEALTHCHECK=1 PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q

test-bd:
	LOCAL_SCRAPER_SKIP_HEALTHCHECK=1 PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q -k bd_

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f backend worker scraper_worker local_scraper postgres redis

migrate:
	docker compose exec backend alembic -c alembic.ini upgrade head

smoke-brightdata:
	docker compose exec backend python backend/scripts/smoke_scraper.py --mode brightdata

smoke-legacy:
	docker compose exec backend python backend/scripts/smoke_scraper.py --mode legacy

smoke: smoke-brightdata
