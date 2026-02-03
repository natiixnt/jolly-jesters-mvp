PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
PYTHONPATH := backend

.PHONY: test test-bd up down logs migrate smoke-brightdata smoke-decodo smoke-legacy smoke

test:
	UI_AUTH_BYPASS=1 LOCAL_SCRAPER_SKIP_HEALTHCHECK=1 PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q

test-bd:
	UI_AUTH_BYPASS=1 LOCAL_SCRAPER_SKIP_HEALTHCHECK=1 PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q -k bd_

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

smoke-decodo:
	docker compose exec backend python backend/scripts/smoke_scraper.py --mode decodo

smoke-legacy:
	docker compose exec backend python backend/scripts/smoke_scraper.py --mode legacy

smoke: smoke-decodo
