# Runbook – lokalny Allegro scraper

## Start/stop
- Wymagane: Docker + `backend/.env` uzupełnione (DB/Redis/proxy).
- Uruchom lokalny scraper (tylko kontener chromowy + API):  
  `LOCAL_SCRAPER_COOLDOWN_SECONDS=5 LOCAL_SCRAPER_LISTING_TIMEOUT=10 LOCAL_SCRAPER_PAGELOAD_TIMEOUT=15 LOCAL_SCRAPER_REQUEST_DELAY=0.5 SELENIUM_PROXY_LIST="" SELENIUM_PROXY="" docker compose up -d local_scraper`
- Sprawdź zdrowie: `curl http://localhost:5050/health` (status `ok` + wersje Chrome/driver).
- Logi: `docker compose logs -f local_scraper` (szukaj `block_reason`, `request_status_code`, `proxy_id`).

## Kluczowe zmienne środowiskowe
- **Proxy/IP:** `SELENIUM_PROXY_LIST` (CSV) lub `SELENIUM_PROXY` (obsługuje `{session}`), `SELENIUM_PROXY_ROTATION_ENABLED` (1/0).  
  Dla testów bez proxy ustaw puste `SELENIUM_PROXY=` i `SELENIUM_PROXY_LIST=`.
- **Profil/rotacja fingerprintu:**  
  - `SELENIUM_PROFILE_ROTATE_MIN_REQUESTS` / `SELENIUM_PROFILE_ROTATE_MAX_REQUESTS` – ile EAN na jednym profilu (domyślnie 4–7).  
  - `SELENIUM_TEMP_PROFILE_DIR` – katalog na profile (ustaw `/data/chrome-profile` aby korzystać z wolumenu).  
  - `FINGERPRINT_ROTATION_EVERY_MIN/MAX` – częstotliwość zmiany UA/viewport/lang.
- **Pacing:** `LOCAL_SCRAPER_REQUEST_DELAY`, `LOCAL_SCRAPER_MIN_INTERVAL_SECONDS`, `LOCAL_SCRAPER_RATE_JITTER_SECONDS`.
- **Retry/timeouts:** `LOCAL_SCRAPER_MAX_ATTEMPTS` (domyślnie 2), `LOCAL_SCRAPER_RETRY_BACKOFF`, `LOCAL_SCRAPER_PAGELOAD_TIMEOUT`, `LOCAL_SCRAPER_LISTING_TIMEOUT`, `LOCAL_SCRAPER_MAX_REQUEST_SECONDS` (HTTP klient).
- **Cooldown:** `LOCAL_SCRAPER_COOLDOWN_SECONDS` (blokady 403/429), `LOCAL_SCRAPER_CAPTCHA_COOLDOWN_SECONDS` (captcha/datadome).

## Rotacja profilu i fingerprintu co 2-3 uruchomienia
- Ustaw: `SELENIUM_PROFILE_ROTATE_MIN_REQUESTS=2`, `SELENIUM_PROFILE_ROTATE_MAX_REQUESTS=3` (profil będzie utrzymywany dla 2-3 EAN, potem nowy katalog w `SELENIUM_TEMP_PROFILE_DIR`).
- Dla fingerprintu: `FINGERPRINT_ROTATION_EVERY_MIN=2`, `FINGERPRINT_ROTATION_EVERY_MAX=3`.
- Restartuj `local_scraper` po zmianie env.

## Smoke/batch testy
- Zapewnij, że klient widzi scraper pod poprawnym URL: `LOCAL_SCRAPER_URL=http://127.0.0.1:5050` (host) lub `http://local_scraper:5050` (wewnątrz sieci docker).
- Smoke 20 EAN (pliki w `/tmp/eans_smoke.txt` lub podaj własny):  
  `LOCAL_SCRAPER_URL=http://127.0.0.1:5050 .venv/bin/python backend/scripts/scraper_smoke.py --ean-file /tmp/eans_smoke.txt --limit 20 --delay 0.5 --jitter 0.2 --output /tmp/smoke_results.csv`
- Batch 200 EAN:  
  `LOCAL_SCRAPER_URL=http://127.0.0.1:5050 .venv/bin/python backend/scripts/scraper_smoke.py --ean-file /tmp/eans_batch.txt --limit 200 --delay 0.1 --jitter 0.05 --output /tmp/batch_results.csv`
- Wyniki: CSV zawiera `outcome`, `block_reason`, `request_status_code`, `duration_seconds`, `proxy_id`, `fingerprint_id`, `attempt`. Jeśli scraper zwróci `retry_after_seconds` (cooldown), runner automatycznie czeka.

## Debug / diagnoza blokad
- Patrz logi `local_scraper`: linie `block_reason=http_403/http_429/datadome/captcha`, `status=403/429`, `proxy_id`.
- Sprawdź ostatni driver debug: GET `/debug` (zawiera args, user_agent, profile info).
- Jeśli `block_reason=http_403` dominuje: zmień proxy/IP (najczęstszy przypadek Datadome). Jeśli `block_reason=captcha/datadome`: rozważ VNC, ręczne przejście ściany i utrzymanie profilu na dłużej (8–12 EAN).
- Jeśli wszystko `cooldown`: skróć `LOCAL_SCRAPER_COOLDOWN_SECONDS` lub wyłącz blokujące proxy; upewnij się, że rotacja proxy faktycznie zmienia IP/sesję.

## Typowy przepływ na VPS
1. Ustaw `.env` z działającą listą proxy (resi z rotacją sesji).  
2. `docker compose up -d local_scraper` (lub cały stack).  
3. Rozgrzej profil: ręcznie rozwiąż captcha przez VNC (opcjonalnie).  
4. Odpal smoke → batch runner; sprawdź CSV i logi.  
5. W razie blokad 403: zmień proxy/listę, zwiększ reuse profilu, ewentualnie podnieś request_delay.
