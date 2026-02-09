# Jolly Jesters MVP (single-scraper)

An Allegro profitability tool built on FastAPI, Celery and PostgreSQL. The entire scraping layer now routes through a single service – `allegro.pl-scraper-main` – served as `allegro_scraper` in Docker.

## Stack
- FastAPI backend + Jinja UI
- Celery worker (queue: `analysis`)
- PostgreSQL + Redis
- Node-based scraper service (`allegro/allegro.pl-scraper-main`)

## Run (dev)
1) Configure env:
```
cp backend/.env.example backend/.env
```
Set scraper connection if different from default:
```
ALLEGRO_SCRAPER_URL=http://allegro_scraper:3000
ALLEGRO_SCRAPER_POLL_INTERVAL=1.0
ALLEGRO_SCRAPER_TIMEOUT_SECONDS=90
```
Scraper needs HTTP proxies. Provide either `PROXIES=http://user:pass@host:port,...` in `.env` (consumed by the scraper container) or mount `proxies.txt` next to the scraper.

2) Start:
```
docker compose up --build
```
Backend listens on `http://localhost` via nginx. Default UI password: `1234`.

Scraper env (set in `.env` if needed):
- `PROXIES` – comma-separated proxy URLs
- `ANYSOLVER_API_KEY` – required for captcha solving
- `SCRAPER_WORKER_COUNT`, `SCRAPER_CONCURRENCY_PER_WORKER`, `SCRAPER_MAX_TASK_RETRIES` – tune Node worker pool

## Flow
UI ➜ `/api/v1/analysis/upload` ➜ Celery `run_analysis_task` ➜ `allegro_scraper` ➜ DB ➜ UI/SSE updates.

## Useful endpoints
- `GET /health` – backend + scraper health snapshot
- `GET /api/v1/status` – scraper status for UI pill
- `GET /api/v1/analysis` – list runs
- `GET /api/v1/analysis/{id}/stream` – live SSE updates

## Tests
```
make test
```

## Notes
- No alternate providers, modes or feature flags remain.
- UI shows only parameters supported by the single scraper (timeout, poll interval, worker/concurrency counts).
