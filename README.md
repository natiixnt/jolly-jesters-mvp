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
Preferred: upload a `.txt` list via UI (Ustawienia ➜ Proxy). It is stored at `/workspace/data/proxies.txt` by default (override `SCRAPER_PROXIES_FILE`), and the scraper reloads it automatically after upload.

Profitability heuristics (backend only, no extra scraping):
- `price_ref` = mediana z 3 najtańszych cen (po odrzuceniu `price <= 0`), fallback: avg(2) / single(1)
- dlaczego: redukcja outlierów vs `min()`
- `multiplier = net_revenue / cost`
- `ROI_po_prowizji = multiplier - 1` (np. `1.5` => `0.5` = 50% ROI po prowizji)
- `PROFITABILITY_MIN_PROFIT_PLN` – minimalny zysk netto (domyślnie 15)
- `PROFITABILITY_MIN_SALES` – minimalna sprzedaż (proxy, domyślnie 3)
- `PROFITABILITY_MAX_COMPETITION` – maks. liczba ofert zwróconych przez scraper (proxy ograniczony limitem wyników; domyślnie 50)

Debug / QA:
- Dodaj `?debug=1`, aby dostać `profitability_debug` w odpowiedziach: `/api/v1/analysis/{id}/results`, `/api/v1/analysis/{id}/results/updates`, `/api/v1/analysis/{id}/stream`, `/api/v1/market-data`.
- `profitability_debug.version = profitability_v2` oznacza obecną logikę (net revenue / cost + progi).
- Definicje: `net_revenue = price_ref * (1 - commission)`, `multiplier = net_revenue / cost`, `ROI = multiplier - 1`.
- Heurystyka `price_ref`: mediana z 3 najtańszych cen (po odrzuceniu `price <= 0`), fallback avg(2) / single(1).

2) Start:
```
docker compose up --build
```
Backend listens on `http://localhost` via nginx. Default UI password: `1234`.

Scraper env (set in `.env` if needed):
- `SCRAPER_PROXIES_FILE` – path to shared proxy list (default `/workspace/data/proxies.txt`)
- `PROXIES` – comma-separated proxy URLs (fallback if no file)
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

## Stable URL (Cloudflare)
To keep one permanent public link (for example `app.schoolmaster.pl`):

1) In Cloudflare Zero Trust create a Named Tunnel and copy its token.
2) In Cloudflare Tunnel -> Public Hostname add:
- Hostname: `app.schoolmaster.pl`
- Service: `http://nginx:80`
3) Put token in `backend/.env`:
```
TUNNEL_TOKEN=<your_token>
```
4) Start/restart stack:
```
docker compose up -d --build
```

Tunnel service is defined in `docker-compose.yml` as `cloudflared` with restart policy `unless-stopped`, so after internet reconnect it should recover automatically and keep the same hostname.
