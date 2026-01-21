# Allegro local scraper – audit (2026-01-21)

## Gdzie jest scraper i jak działa
- Endpoint ASGI: `backend/local_scraper_service.py` (FastAPI) uruchamiany w kontenerze `local_scraper` (`docker-compose.yml`). Kluczowe miejsca: sloty/limity `_scrape_slot` i throttling `_wait_for_rate_limit` (linia ok. 60), blokady/cooldown `_mark_blocked` (linia ok. 80) oraz klasyfikacja blokad `_detail_indicates_block` (linia 144) przed zwróceniem odpowiedzi.
- Właściwe scrapowanie: `backend/main.py`, funkcja `_create_driver` (ok. linia 527) konfiguruje Chromedriver + SeleniumWire. Fingerprint + proxy: `get_selenium_fingerprint()` i `get_selenium_proxy()` z `backend/app/utils/fingerprint.py` (linia 445 i 339). Nowy stealth script i user-agent override w `_apply_stealth`.
- Flow od taska do wyniku: Celery task `scrape_one_local` w `backend/app/workers/tasks.py` (ok. linia 305) wywołuje `fetch_via_local_scraper` → FastAPI `/scrape` → `scrape_single_ean` w `backend/main.py` → zwraca słownik z wynikami/flagami → task `_log_scrape_outcome` zapisuje status do DB i logów.
- Klient HTTP do lokalnego scrapera: `backend/app/utils/local_scraper_client.py` (timeouty, backoff), używany zarówno przez taski, jak i nowy smoke runner `backend/scripts/scraper_smoke.py`.

## Konfiguracja przeglądarki / fingerprint / profil
- **Profil Chrome:** nowe API w `fingerprint.py` (`_next_profile_dir`, linia 349) utrzymuje jeden tymczasowy profil na 4-7 żądań (konfigurowalne env: `SELENIUM_PROFILE_ROTATE_MIN_REQUESTS`, `SELENIUM_PROFILE_ROTATE_MAX_REQUESTS`), a dopiero potem rotuje katalog. Wcześniej profil był tworzony na każdy request, co usuwało ciasteczka/Datadome i dawało powtarzalny “nowy” fingerprint → wysokie block rate.
- **Ścieżka profilu:** `SELENIUM_TEMP_PROFILE_DIR` domyślnie wskazuje teraz na wolumen `/data/chrome-profile` (compose), więc sesje można utrzymać między restartami kontenera.
- **Fingerprint:** rotacja UA/viewport/lang zostawiona, ale z dodatkową telemetrią (ua_hash, ua_version) i logiem reuse profilu. Stealth: `_apply_stealth` w `main.py` wstrzykuje `navigator.webdriver/languages/platform/plugins` + `Network.setUserAgentOverride`.
- **Proxy:** `get_selenium_proxy` nadal wspiera listę lub szablon `{session}`; dodano `force_rotate_selenium_proxy` do wymuszenia zmiany przy retrach/blokadach.
- **Env .env fix:** `backend/app/core/config.py` wczytuje teraz poprawnie `backend/.env` (wcześniej patrzył w `backend/app/.env`, więc lokalne skrypty nie widziały zmiennych takich jak `LOCAL_SCRAPER_URL` czy `LOCAL_SCRAPER_ENABLED`).

## Pacing / retry / klasyfikacja wyników
- **Retry per EAN:** `scrape_single_ean` korzysta z `_scrape_attempt` (linia 951) z domyślnie 2 prób (`LOCAL_SCRAPER_MAX_ATTEMPTS`, backoff `LOCAL_SCRAPER_RETRY_BACKOFF`). Przy blokadach wymusza rotację fingerprint/proxy/profilu.
- **Wczesna detekcja blokady:** `_request_metadata` zbiera status HTTP z SeleniumWire; jeśli 403/429 pojawi się od razu, `_scrape_attempt` zwraca blokadę bez czekania pełnego `WebDriverWait`.
- **Klasyfikacja:** `_detect_block_reason` uwzględnia statusy HTTP (403/429/5xx), nagłówki Datadome, captcha i “minimal page”. `_status_indicates_not_found` reaguje na 404; `_detect_no_results_text` zostawione jako fallback.
- **Wait strategy:** timeouty `LOCAL_SCRAPER_PAGELOAD_TIMEOUT`/`LOCAL_SCRAPER_LISTING_TIMEOUT` są parametryzowane w compose (wcześniej zaszyte 45/50).
- **Throttling:** globalny min interval + jitter (`LOCAL_SCRAPER_RATE_JITTER_SECONDS`) w `local_scraper_service.py` (linia 40) + opcjonalny request delay. Cooldown per blokada/captcha nadal sterowany env.

## Obserwowalność
- Odpowiedź `/scrape` zwraca teraz: `block_reason`, `request_status_code`, `stage_durations`, `proxy_id/source`, `attempt/max_attempts`, `retry_after_seconds`.
- Logi: `_log_scrape_outcome` w taskach loguje `block_reason` i status HTTP; scraper loguje wynik z licznością ofert, block_reason, fingerprint_id, proxy_id. Debug driver zawiera profil reuse i proxy.
- Smoke/batch runner: `backend/scripts/scraper_smoke.py` – odpala listę EAN, zapisuje CSV (durations, block_reason, status) i respektuje Retry-After z cooldownu.

## Wyniki testów E2E (obecna sieć)
Środowisko: kontener `local_scraper` bez proxy (lokalny IP) + krótsze timeouty (pageload 15s, listing 10s), cooldown 2s, profile rotacja 4-7. Lista EAN syntetyczna (brak pewnych hitów, celem obserwacja blokad).

| Test | Plik wyników | Liczba EAN | OK | Not_found | Blocked | Error | Śr. czas/EAN | Dominujące block_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Smoke (20) | `/tmp/smoke_results.csv` | 20 | 0 | 0 | 20 | 0 | 4.98s | 50% `http_403`, 50% `cooldown` (cooldown po pierwszym 403) |
| Batch (200) | `/tmp/batch_results.csv` | 200 | 0 | 0 | 199 | 1 | 5.55s | 50% `http_403`, 49.5% `cooldown`, 1 `error` (bez reason) |

Logi `docker compose logs local_scraper` pokazują natychmiastowe 403 z Datadome (`block_reason=http_403`) nawet bez proxy → obecny IP jest twardo zablokowany.

## Problemy (priorytet)
- **P0 – IP/Datadome 403 na każdą próbę**: bez działającego proxy lub świeżego IP scraper natychmiast wpada w blokadę (wyniki powyżej). Obecny statyczny proxy z `.env` (`core-residential.evomi.com`) też zwraca 403. Wymagana zmiana puli proxy lub sesji (szablon `{session}`/lista resi), ewentualnie ręczne odblokowanie/captcha.
- **P0 – cooldown kaskadowy**: jeden 403 ustawia cooldown i kolejne EAN’y lecą jako 429 `cooldown`. Zredukowałem ekspozycję (cooldown konfigurowalny + szybka detekcja), ale bez “zdrowego” IP blokada pozostaje całkowita.
- **P1 – brak poprawnych wyników sukces/not_found w obecnej sieci**: nie da się zweryfikować parsowania/offers, bo nie przechodzimy przez wall.
- **P1 – wcześniejszy błąd .env**: hostowe skrypty nie ładowały `backend/.env` (naprawione), więc lokalne health/smoke mogły wcześniej mówić “disabled”.

## Rekomendacje / quick wins
1. **Proxy/IP**: podmień na działające residentiale z rotacją sesji (`SELENIUM_PROXY_LIST` lub `SELENIUM_PROXY` z `{session}`), ewentualnie pozwól na fallback bez proxy tylko z domowym IP. Przy problemach Datadome rozważyć ręczne przejście captcha (VNC) + utrzymanie profilu przez kilka żądań.
2. **Profile**: zostaw rotację 4-7 żądań na profilu w wolumenie `/data/chrome-profile`; zwiększ do 8-12 jeśli Datadome nadal wymaga stabilniejszej sesji.
3. **Pacing**: utrzymuj `LOCAL_SCRAPER_REQUEST_DELAY` 0.5-1.5s + jitter (`LOCAL_SCRAPER_RATE_JITTER_SECONDS`) oraz `LOCAL_SCRAPER_COOLDOWN_SECONDS` ≈ 5-15s dla błędów 403/429; osobny dłuższy cooldown dla captcha.
4. **Rotacja po blokadzie**: włącz szablon proxy session lub listę, tak aby retry w `_scrape_attempt` faktycznie zmieniało IP. W razie braku listy zwiększ `LOCAL_SCRAPER_MAX_ATTEMPTS` do 3-4 dopiero gdy IP będzie zdrowe.
5. **Monitoring**: obserwuj `block_reason`, `request_status_code`, `proxy_id` w logach/CSV – jeśli dominuje `http_403`, problem jest sieciowy, nie aplikacyjny.

## Kryteria akceptacji (propozycja, do potwierdzenia z działającym proxy)
- Blokady (`blocked`) < **5%** na batch 200 przy rotacji proxy+profilu.
- `success + not_found` ≥ **95%**.
- Średni czas/EAN (z retry) **≤ 15s** przy `LOCAL_SCRAPER_MAX_ATTEMPTS=2-3`, `WINDOWS=1`.
- Brak serii 403/429 dla wszystkich EAN (twardy datacenter IP) – jeśli wystąpi, automatyczny switch proxy/session i skrócony cooldown.
