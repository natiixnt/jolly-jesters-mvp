# Jolly Jesters MVP

Offline-first analiza oplacalnosci Allegro z FastAPI, PostgreSQL, Redis i Celery.

## Uruchomienie (dev)

```
docker compose up --build
```

Backend startuje z automatycznym `alembic upgrade head`. Glowne UI (FastAPI + Jinja) jest pod `http://localhost:8000/`. Stary widok Streamlit (port 8501) moze zostac do celow dev, ale podstawowa sciezka uzytkownika to HTML z backendu.

Po zmianach zaleznosci Pythona zbuduj na nowo obrazy backend/worker:
```
docker compose build --no-cache backend worker
```

## Migracje bazy (Alembic)

- Zalecany sposob uruchamiania migracji: **tylko z poziomu kontenera backendu**, aby `app` bylo na PYTHONPATH:
  ```
  docker compose exec pilot_backend alembic upgrade head
  ```
- Alternatywnie mozna uzyc skryptu pomocniczego (takze **wewnatrz** kontenera):
  ```
  docker compose exec pilot_backend bash backend/scripts/migrate.sh
  ```
- Nie uruchamiaj `alembic upgrade head` bezposrednio z hosta (czeste bledy `ModuleNotFoundError: app` / Pydantic). Uzywaj polecenia z `docker compose exec`.

## Wymagane zmienne srodowiskowe

| Klucz | Opis |
| --- | --- |
| `DB_URL` | URL do Postgresa (np. `postgresql+psycopg2://pilot:pilot@pilot_postgres:5432/pilotdb`) |
| `REDIS_URL` | URL do Redisa (np. `redis://redis:6379/0`) |
| `ALLEGRO_API_TOKEN` | Token Bearer do Allegro API (opcjonalnie) |
| `PROXY_LIST` | Lista proxy rozdzielona przecinkiem dla cloud HTTP (opcjonalnie) |
| `LOCAL_SCRAPER_ENABLED` | `true/false` - wlacza lokalny scraper Selenium uruchamiany na hoscie |
| `LOCAL_SCRAPER_URL` | Bazowy adres lokalnego scrapera (bez `/scrape` na koncu), np. `http://host.docker.internal:5050` |
| `WORKSPACE` | Katalog roboczy na upload/export (domyslnie `/workspace`) |
| `EUR_TO_PLN_RATE` | Staly kurs przeliczenia EUR/PLN dla importu cennikow (domyslnie `4.5`) |

## Lokalny scraper Selenium (host)

1. Na hoście utworz virtualenv i zainstaluj zaleznosci:
   ```
   python -m venv .venv
   .\\.venv\\Scripts\\activate  # Windows (cmd/PowerShell)
   source .venv/bin/activate   # Linux/Mac
   pip install -r backend/requirements.txt
   ```
2. Uruchom serwis HTTP scrapera (udostepnia endpoint `/scrape`):
   ```
   uvicorn local_scraper_service:app --host 0.0.0.0 --port 5050
   ```
3. Upewnij sie, ze backend i worker w Dockerze widza hosta:
   - `LOCAL_SCRAPER_ENABLED=true`
   - `LOCAL_SCRAPER_URL=http://host.docker.internal:5050`
   - `docker compose up` ustawi `host.docker.internal` poprzez `extra_hosts` (juz w pliku `docker-compose.yml`).
4. (Opcjonalnie) Kontenerowa wersja scrapera: `docker compose --profile local-scraper-container up local_scraper` (domyslnie nie jest uruchamiana, aby nie blokowac portu 5050 na hoście).

### Linux notes
- `host.docker.internal` wymaga `extra_hosts: ["host.docker.internal:host-gateway"]` – jest juz dodane w docker-compose dla backend/worker. Wlacz w Docker Engine experimental features, jesli host-gateway jest niedostepny.
- Scraper na hoście musi nasluchiwac na `0.0.0.0:5050` (nie tylko 127.0.0.1), aby kontenery mogly sie polaczyc.
- Sprawdz firewall/ufw – port 5050 lokalnie musi byc otwarty dla ruchu z bridge Docker (zwykle 172.17.0.0/16).
- Szybki test z kontenera backendu: `docker compose exec pilot_backend curl -v http://host.docker.internal:5050/health`

## Minimalny flow (cURL)

1) Utworz kategorie:
```bash
curl -X POST http://localhost:8000/api/v1/categories/ \
  -H "Content-Type: application/json" \
  -d '{"name":"Perfumy","profitability_multiplier":1.5,"commission_rate":0.1}'
```

2) Wyslij plik Excel (kolumny: EAN, Name, PurchasePrice):
```bash
curl -X POST http://localhost:8000/api/v1/analysis/upload \
  -F "category_id=<ID_Z_KROKU_1>" \
  -F "file=@/path/to/input.xlsx" \
  -F "mode=mixed" \
  -F "use_api=true" \
  -F "use_cloud_http=true" \
  -F "use_local_scraper=true"
```
Zwraca `analysis_run_id`.

3) Sprawdzaj status:
```bash
curl http://localhost:8000/api/v1/analysis/<RUN_ID>
```

4) Po `status=completed` pobierz raport:
```bash
curl -o output.xlsx http://localhost:8000/api/v1/analysis/<RUN_ID>/download
```
