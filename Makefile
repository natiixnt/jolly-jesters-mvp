PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
PYTHONPATH := backend

.PHONY: test up down logs migrate

test:
	UI_AUTH_BYPASS=1 PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f backend worker allegro_scraper postgres redis

migrate:
	docker compose exec backend alembic -c alembic.ini upgrade head
