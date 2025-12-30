# Jolly Jesters MVP

Offline-first analiza oplacalnoœci Allegro z FastAPI, PostgreSQL, Redis i Celery.
Nie korzystamy z oficjalnego API; scraping działa przez proxy/cloud HTTP oraz lokalny Selenium.

## Uruchomienie (dev)

```
docker compose up --build
```

Backend i workery uruchamiaja sie z kodu w obrazie (bez bind mount), wiec po zmianach w `backend/` wykonaj:
```
docker compose build backend worker scraper_cloud_worker scraper_worker
```

## Troubleshooting (macOS / Docker Desktop Errno 35)

- Jeśli widzisz `OSError: [Errno 35] Resource deadlock avoided` przy imporcie, nie używaj bind mount na `/app` i przebuduj obrazy.
- W Docker Desktop ustaw File Sharing na VirtioFS i wyłącz gRPC FUSE, potem zrestartuj Docker Desktop.
- Unikaj `--reload` w Uvicorn/Celery na macOS; używaj ręcznych restartów kontenerów.
- Jeśli UI ubija kontenery (kod 137), zmniejsz concurrency workerów lub zwiększ RAM w Docker Desktop.
- Jeśli koniecznie potrzebujesz bind mount (live edit), dodaj override z `./backend:/app` tylko lokalnie i licz się z niestabilnością.

Backend startuje z automatycznym `alembic upgrade head`. Glowne UI (FastAPI + Jinja) jest pod `http://localhost:8000/`. Stary widok Streamlit (port 8501) moze zostac do celow dev, ale podstawowa sciezka uzytkownika to HTML z backendu.

Po zmianach zaleznosci Pythona zbuduj na nowo obrazy backend/worker:
```
docker compose build --no-cache backend worker
```

## Analiza z bazy + zarzadzanie runami

- W UI w glownym formularzu dostepny jest blok **Analiza z bazy**:
  - wybierz kategorie, tryb i strategie jak zwykle,
  - ustaw filtry (ostatnie N dni, wszystkie zapisane, tylko z udanymi danymi, limit),
  - kliknij "Analiza z bazy" aby uruchomic run bez uploadu pliku.
- W trakcie runu mozna go anulowac (przycisk "Anuluj" w panelu statusu lub w historii).
- Dla runow z bledami dostepne jest "Retry" (menu Operacje w historii).

API:
```
POST /api/v1/analysis/run_from_cache (alias: /api/v1/analysis/run_from_db, /api/v1/analysis/start_from_db)
POST /api/v1/analysis/{id}/cancel
POST /api/v1/analysis/{id}/retry_failed?strategy=cloud|local
GET /api/v1/analysis/active
```

## Kolejki Celery (architektura)

- `analysis`: uruchamia `run_analysis_task` i planuje per-item scraping.
- `scraper_cloud`: HTTP/proxy scraping (task `scrape_one_cloud`).
- `scraper_local`: lokalne Selenium (task `scrape_one_local`) w osobnym workerze.

## Local scraper w Dockerze (VPS/Prod - domyslnie)

`docker compose up --build` uruchamia serwis `local_scraper` razem z backendem i workerami.
Scraper dziala w trybie headed na wirtualnym display (Xvfb).

Porty:
- `5050` - API scrapera
- `6080` - opcjonalny noVNC (rebuild z `LOCAL_SCRAPER_WITH_VNC=1`, potem ustaw `LOCAL_SCRAPER_ENABLE_VNC=1`;
  tylko localhost + SSH tunnel/firewall)

Checklista (z kontenera backendu):
```
docker compose exec backend curl -v http://local_scraper:5050/health
```

Profil Chrome jest zapisywany w wolumenie `local_scraper_profile`
(`SELENIUM_USER_DATA_DIR=/data/chrome-profile`) i przetrwa restart kontenera.

### Uzycie profilu z hosta (bind mount, opcjonalnie)

Jesli chcesz użyć istniejacego profilu Chrome z hosta (np. po udanym rozwiazaniu captcha),
utwórz `docker-compose.override.yml` i podmontuj katalog profilu:
```
services:
  local_scraper:
    volumes:
      - ~/.local-scraper-profile:/data/chrome-profile
```
Uwaga: zadbaj o uprawnienia (Docker musi mieć dostęp do katalogu). Profil z macOS/Windows
moze nie dzialac w kontenerze Linux (inne sciezki/formaty) - najlepiej uzywac profilu
utworzonego na Linux/host.

### Linux: dopasowanie IP hosta (network_mode: host, opcjonalnie)

Na Linuxie mozesz uruchomic kontener local_scraper w trybie host network:
```
docker compose -f docker-compose.yml -f docker-compose.local-scraper-hostnet.yml up --build
```
W tym trybie ustaw `LOCAL_SCRAPER_URL=http://host.docker.internal:5050` (lub adres hosta),
bo nazwa serwisu `local_scraper` nie dziala w sieci hosta.

### Manualne rozwiazanie captcha przez noVNC

1) Zbuduj obraz z VNC: `LOCAL_SCRAPER_WITH_VNC=1 docker compose build local_scraper`
2) Uruchom z VNC: `LOCAL_SCRAPER_ENABLE_VNC=1 docker compose up -d local_scraper`
3) Otworz `http://127.0.0.1:6080` (jesli widzisz directory listing, wejdz w `/vnc.html`)
   i rozwiaz captcha w oknie Chrome.
4) Uruchom ponownie analize w UI (ten sam profil zostaje w `/data/chrome-profile`).
Jeśli Chrome nie startuje, ustaw `SELENIUM_CHROME_LOG_PATH=/tmp/chrome.log` w serwisie
`local_scraper` i sprawdz log w kontenerze.

Jeśli dalej widzisz `SessionNotCreatedException`, mozna wlaczyc fallback na tymczasowy profil:
`SELENIUM_PROFILE_FALLBACK=1` (Chrome spróbuje uruchomic sie na nowym profilu w `/tmp`).
Opcjonalnie ustaw `SELENIUM_CHROMEDRIVER_LOG_PATH=/tmp/chromedriver.log` zeby zobaczyc logi
chromedrivera.

Jeśli log pokazuje, że profil jest zajety przez inny proces, ustaw
`SELENIUM_KILL_EXISTING=1` (kontener spróbuje zakończyć pozostale procesy Chrome
używające tego samego profilu).

### VPS/DC IP (ważne)

Na VPS/datacenter IP captcha moze pojawiac sie nadal mimo headed. Rozważ uruchomienie local_scraper
na zaufanej sieci domowej (host mode) albo użycie proxy rezydencjalnych w trybie cloud/proxy.

## Host scraper (Windows/macOS/Linux - opcjonalnie dev)

Jesli chcesz uruchomic Chrome na hoscie (np. macOS) zamiast w Dockerze,
uruchom scraper z repo root i ustaw `LOCAL_SCRAPER_URL` na hosta.

Recommended (cross-platform) command (requires deps from `backend/requirements.txt`):
```
python -m uvicorn host_scraper:app --host 0.0.0.0 --port 5050
```

Scripts (preferred, they install deps automatically; run from repo root):
- macOS/Linux: `./backend/scripts/run_local_scraper.sh`
- Windows (PowerShell): `.\backend\scripts\run_local_scraper.ps1`
  - If ExecutionPolicy blocks scripts, run:
    `powershell -ExecutionPolicy Bypass -File .\backend\scripts\run_local_scraper.ps1`
    or `Set-ExecutionPolicy -Scope Process Bypass`.

Then start Docker services (host scraper mode):
```
./scripts/dev_up.sh
```

Smoke tests (repo root):
```
python -c "from host_scraper import app; print(app)"
curl http://127.0.0.1:5050/health
curl -X POST http://127.0.0.1:5050/scrape -H "Content-Type: application/json" -d '{"ean":"5901234123457"}'
```

Docker verification (from container, host mode):
```
docker compose exec backend curl -v http://host.docker.internal:5050/health
```
On Linux, ensure `extra_hosts: host.docker.internal:host-gateway` is present in
`docker-compose.yml` (already configured here).

Env diagnostics (from container):
```
docker compose exec backend sh -lc 'printenv | grep LOCAL_SCRAPER'
docker compose exec scraper_worker sh -lc 'printenv | grep LOCAL_SCRAPER'
```

Notes:
- Allegro headless często kończy się captcha; rekomendowany jest tryb headed.
- W Dockerze headed działa przez Xvfb; opcjonalnie ustaw `LOCAL_SCRAPER_ENABLE_VNC=1`
  i otwórz http://127.0.0.1:6080 (zalecany SSH tunnel / firewall).
- The host script runs Chrome in headed mode so you can solve captcha if needed.
- To keep cookies, set `SELENIUM_USER_DATA_DIR=~/.local-scraper-profile`.
- The script installs required dependencies automatically (brew/apt/dnf/pacman/zypper/apk).
  You may be prompted for sudo on Linux.
- If chromedriver is missing, the script downloads a matching version from
  Chrome-for-Testing into `~/.local/bin`.
- On Ubuntu/Debian, if Chromium is missing and snap isn't installed, the script
  will install `snapd` and try to install Chromium via snap.
- To persist `~/.local/bin` in PATH, set `LOCAL_SCRAPER_PERSIST_PATH=1`
  before running the script.
- If port `5050` is in use, run the scraper with a different port, e.g.:
  `./backend/scripts/run_local_scraper.sh 5051` or
  `.\backend\scripts\run_local_scraper.ps1 5051`
  and set `LOCAL_SCRAPER_URL=http://host.docker.internal:5051` (or
  `LOCAL_SCRAPER_PORT=5051 ./scripts/dev_up.sh`).
- The scraper script updates `backend/.env` only when you set
  `LOCAL_SCRAPER_UPDATE_ENV=1` explicitly.
  The compose wrapper does the same if you pass `LOCAL_SCRAPER_PORT` or
  `LOCAL_SCRAPER_URL` and set `LOCAL_SCRAPER_UPDATE_ENV=1`.
  To update a different env file, set `LOCAL_SCRAPER_ENV_FILE=/path/to/.env`.
W trybie hostowym ustaw `LOCAL_SCRAPER_URL=http://host.docker.internal:5050`
(albo inny port), zeby kontenery widzialy scraper.

## Migracje bazy (Alembic)

- Zalecany sposob uruchamiania migracji: **tylko z poziomu kontenera backendu**, aby `app` bylo na PYTHONPATH:
  ```
  docker compose exec backend alembic upgrade head
  ```
- Alternatywnie mozna uzyc skryptu pomocniczego (takze **wewnatrz** kontenera):
  ```
  docker compose exec backend bash backend/scripts/migrate.sh
  ```
- Nie uruchamiaj `alembic upgrade head` bezposrednio z hosta (czeste bledy `ModuleNotFoundError: app` / Pydantic). Uzywaj polecenia z `docker compose exec`.

## Wymagane zmienne srodowiskowe

| Klucz | Opis |
| --- | --- |
| `DB_URL` | URL do Postgresa (np. `postgresql+psycopg2://pilot:pilot@pilot_postgres:5432/pilotdb`) |
| `REDIS_URL` | URL do Redisa (np. `redis://redis:6379/0`) |
| `PROXY_LIST` | Lista proxy rozdzielona przecinkiem dla cloud HTTP (opcjonalnie) |
| `LOCAL_SCRAPER_ENABLED` | `true/false` – w³¹cza lokalny scraper Selenium |
| `LOCAL_SCRAPER_URL` | Bazowy URL lokalnego scrapera, np. `http://local_scraper:5050` |
| `WORKSPACE` | Katalog roboczy na upload/export (domyœlnie `/workspace`) |
| `EUR_TO_PLN_RATE` | Sta?y kurs przeliczenia EUR?PLN dla importu cennik?w (domy?lnie `4.5`) |

## Diagnostyka (checklista)

1) Local scraper (Docker):
```
docker compose exec backend curl -v http://local_scraper:5050/health
docker compose exec backend curl -v http://local_scraper:5050/debug
```
2) Host scraper (opcjonalnie dev):
```
curl http://127.0.0.1:5050/health
curl http://127.0.0.1:5050/debug
```
3) ENV w kontenerach:
```
docker compose exec backend sh -lc 'printenv | grep LOCAL_SCRAPER'
docker compose exec scraper_worker sh -lc 'printenv | grep LOCAL_SCRAPER'
```
4) Logi kolejek:
```
docker compose logs -f local_scraper scraper_worker backend
```

5) Fail-fast (gdy local scraper nie dziala):
```
docker compose stop local_scraper
curl -s -X POST http://localhost:8000/api/v1/analysis/upload \
  -F "category_id=<ID_Z_KROKU_1>" \
  -F "file=@/path/to/input.xlsx" \
  -F "mode=mixed" \
  -F "use_cloud_http=false" \
  -F "use_local_scraper=true"
docker compose start local_scraper
```
Oczekiwane: HTTP 400 z komunikatem o niedostepnym local scraperze.

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
