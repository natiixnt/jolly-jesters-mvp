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
