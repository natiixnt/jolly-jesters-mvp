# Jolly Jesters MVP

Offline-first analiza oplacalnoœci Allegro z FastAPI, PostgreSQL, Redis i Celery.

## Uruchomienie (dev)

```
docker-compose up --build
```

Backend i workery uruchamiaja sie z kodu w obrazie (bez bind mount), wiec po zmianach w `backend/` wykonaj:
```
docker compose build backend worker scraper_worker
```

## Troubleshooting (macOS / Docker Desktop Errno 35)

- Jeśli widzisz `OSError: [Errno 35] Resource deadlock avoided` przy imporcie, nie używaj bind mount na `/app` i przebuduj obrazy.
- W Docker Desktop ustaw File Sharing na VirtioFS i wyłącz gRPC FUSE, potem zrestartuj Docker Desktop.
- Unikaj `--reload` w Uvicorn/Celery na macOS; używaj ręcznych restartów kontenerów.
- Jeśli koniecznie potrzebujesz bind mount (live edit), dodaj override z `./backend:/app` tylko lokalnie i licz się z niestabilnością.

Backend startuje z automatycznym `alembic upgrade head`. Glowne UI (FastAPI + Jinja) jest pod `http://localhost:8000/`. Stary widok Streamlit (port 8501) moze zostac do celow dev, ale podstawowa sciezka uzytkownika to HTML z backendu.

## Local scraper on host (recommended for dev)

If Allegro blocks headless Chrome in Docker, run the local scraper on the host.
This is the default setup in `docker-compose.yml`.

1) Start local scraper outside Docker:
```
./backend/scripts/run_local_scraper.sh
```

2) Start Docker services (host scraper is default):
```
./scripts/dev_up.sh
```

Notes:
- The script runs Chrome in headed mode so you can solve captcha if needed.
- To keep cookies, set `SELENIUM_USER_DATA_DIR=~/.local-scraper-profile`.
- The script installs required dependencies automatically (brew/apt/dnf/pacman/zypper/apk).
  You may be prompted for sudo on Linux.
- If chromedriver is missing, the script downloads a matching version from
  Chrome-for-Testing into `~/.local/bin`.
- On Ubuntu/Debian, if Chromium is missing and snap isn't installed, the script
  will install `snapd` and try to install Chromium via snap.
- To persist `~/.local/bin` in PATH, set `LOCAL_SCRAPER_PERSIST_PATH=1`
  before running the script.
- If port `5050` is in use, run:
  `LOCAL_SCRAPER_PORT=5051 ./backend/scripts/run_local_scraper.sh`
  and start compose with:
  `LOCAL_SCRAPER_PORT=5051 ./scripts/dev_up.sh`
- The scraper script auto-updates `backend/.env` with the chosen port.
  The compose wrapper also updates `backend/.env` if you pass
  `LOCAL_SCRAPER_PORT` or `LOCAL_SCRAPER_URL`.
  To skip this, set `LOCAL_SCRAPER_UPDATE_ENV=0`.
  To update a different env file, set `LOCAL_SCRAPER_ENV_FILE=/path/to/.env`.
- To run the scraper inside Docker instead, use:
  `docker compose --profile container-scraper up --build` and set
  `LOCAL_SCRAPER_URL=http://local_scraper:5050`.

## Migracje bazy (Alebmic)

- Zalecany sposob uruchamiania migracji: **tylko z poziomu kontenera backendu**, aby `app` by'o na PYTHONPATH:
  ```
  docker compose exec pilot_backend alembic upgrade head
  ```
- Alternatywnie mo'na u'y' skryptu pomocniczego (tak'e **wewn'trz** kontenera):
  ```
  docker compose exec pilot_backend bash backend/scripts/migrate.sh
  ```
- Nie uruchamiaj `alembic upgrade head` bezpo'rednio z hosta (czeste b''dy `ModuleNotFoundError: app` / Pydantic). U'ywaj polecenia z `docker compose exec`.

## Wymagane zmienne srodowiskowe

| Klucz | Opis |
| --- | --- |
| `DB_URL` | URL do Postgresa (np. `postgresql+psycopg2://pilot:pilot@pilot_postgres:5432/pilotdb`) |
| `REDIS_URL` | URL do Redisa (np. `redis://redis:6379/0`) |
| `ALLEGRO_API_TOKEN` | Token Bearer do Allegro API (opcjonalnie) |
| `PROXY_LIST` | Lista proxy rozdzielona przecinkiem dla cloud HTTP (opcjonalnie) |
| `LOCAL_SCRAPER_ENABLED` | `true/false` – w³¹cza lokalny scraper Selenium |
| `LOCAL_SCRAPER_URL` | Endpoint lokalnego scrapera, np. `http://host.docker.internal:5050/scrape` |
| `WORKSPACE` | Katalog roboczy na upload/export (domyœlnie `/workspace`) |
| `EUR_TO_PLN_RATE` | Sta?y kurs przeliczenia EUR?PLN dla importu cennik?w (domy?lnie `4.5`) |

## Minimalny flow (cURL)

1) Utwórz kategoriê:
```bash
curl -X POST http://localhost:8000/api/v1/categories/ \
  -H "Content-Type: application/json" \
  -d '{"name":"Perfumy","profitability_multiplier":1.5,"commission_rate":0.1}'
```

2) Wyœlij plik Excel (kolumny: EAN, Name, PurchasePrice):
```bash
curl -X POST http://localhost:8000/api/v1/analysis/upload \
  -F "category_id=<ID_Z_KROKU_1>" \
  -F "file=@/path/to/input.xlsx" \
  -F "mode=mixed" \
  -F "use_api=true" \
  -F "use_cloud_http=true" \
  -F "use_local_scraper=true"
```
Zwróci `analysis_run_id`.

3) Sprawdzaj status:
```bash
curl http://localhost:8000/api/v1/analysis/<RUN_ID>
```

4) Po `status=completed` pobierz raport:
```bash
curl -o output.xlsx http://localhost:8000/api/v1/analysis/<RUN_ID>/download
```
