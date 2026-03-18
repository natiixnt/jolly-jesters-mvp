# Checklist Audytu Technicznego  -Jolly Jesters MVP

Data: 2026-03-18
Wersja: 1.0

---

## A. Koszt i efektywność operacyjna

### A1. Metering kosztów
- [ ] Czy system mierzy koszt przetwarzania per EAN?
  - **Stan:** BRAK. Ani `AnalysisRunItem` ani scraper Stats nie mają pola `cost`.
  - **Ryzyko:** Brak podstaw pod pricing i kontrolę OPEX.
- [ ] Czy system mierzy koszt per 1000 EAN?
  - **Stan:** BRAK. Brak jakiegokolwiek agregatora kosztowego.
- [ ] Czy znany jest koszt CAPTCHA per request?
  - **Stan:** Scraper liczy `totalCaptchaSolves` (Stats), ale nie przelicza na PLN/USD.
  - **Dane:** AnySolver API  -koszt per solve konfigurowalny (env).
- [ ] Czy koszt jest powtarzalny między runami?
  - **Stan:** NIEWERYFIKOWALNE  -brak danych do porównania.

### A2. Wpływ retry i weryfikacji na koszt
- [ ] Czy retry rate jest mierzony?
  - **Stan:** Scraper liczy `task.retries` wewnętrznie, ale NIE zwraca tego w API response.
  - **Plik:** `allegro.pl-scraper-main/src/api/routes.ts:26-36`
- [ ] Czy access_challenge_rate (CAPTCHA) jest mierzony per run?
  - **Stan:** BRAK. `captchaSolves` jest w `AllegroResult` ale NIE jest zapisywany do DB.
  - **Plik:** `backend/app/workers/tasks.py:59-111`  -`_apply_result()` pomija `duration_ms` i `captcha_solves`.
- [ ] Czy wpływ jakości IP na koszt jest mierzalny?
  - **Stan:** BRAK. Proxy rotation jest round-robin bez scoringu.

### A3. Identyfikacja hotspotów kosztowych
- [ ] Czy zidentyfikowano miejsca generujące największy koszt?
  - **Stan:** Brak danych. Potencjalne hotspoty:
    1. CAPTCHA solving (AnySolver)  -koszt per solve
    2. Proxy bandwidth  -niezmierzony
    3. Session resets  -powodują dodatkowe requesty
- [ ] Czy istnieje cache per EAN redukujący powtórne scrapy?
  - **Stan:** CZĘŚCIOWY. `cache_ttl_days` w `Setting` kontroluje re-użycie `ProductMarketData`, ale działa tylko w trybie `run_from_db`. Upload zawsze scrapuje.

---

## B. Wydajność i przepustowość

### B1. Realna przepustowość
- [ ] Jaka jest realna przepustowość EAN/min?
  - **Stan:** NIEZMIERZONA per run. Scraper ma `tasksPerHour` w Stats (rolling 60s window), ale to metryka globalna.
  - **Konfiguracja:** `WORKER_COUNT=1`, `CONCURRENCY_PER_WORKER=1`, Celery `--concurrency=1`.
  - **Plik:** `docker-compose.yml`  -worker command.
- [ ] Czy EAN/min jest raportowany w UI?
  - **Stan:** BRAK.

### B2. Bottlenecki
- [ ] Celery concurrency
  - **Stan:** `--concurrency=1`  -jeden run na raz, sekwencyjne przetwarzanie EAN.
  - **Plik:** `docker-compose.yml`
- [ ] Scraper concurrency
  - **Stan:** Default `WORKER_COUNT=1`, `CONCURRENCY_PER_WORKER=1`. Throttling na scrapeCount 0 i 4-6.
  - **Plik:** `allegro.pl-scraper-main/src/worker/worker.ts:22-24`
- [ ] Warstwa sieciowa (proxy)
  - **Stan:** Round-robin, max 50 proxy attempts per task. Brak scoring, brak quarantine.
  - **Plik:** `allegro.pl-scraper-main/src/worker/worker.ts:99-124`
- [ ] Polling overhead
  - **Stan:** Backend polluje scraper co `ALLEGRO_SCRAPER_POLL_INTERVAL` (default 1s). Przy 90s timeout to max 90 polls per EAN.
  - **Plik:** `backend/app/utils/allegro_scraper_client.py:169-226`

### B3. Skalowalność
- [ ] Czy system skaluje się przy większym wolumenie?
  - **Stan:** OGRANICZONA. Celery `concurrency=1` = max 1 run jednocześnie. Scraper jest single-instance.
- [ ] Czy jest limit per user?
  - **Stan:** BRAK. Każdy user może uruchomić dowolną liczbę runów.

---

## C. Stabilność i odporność systemu

### C1. Retry rate
- [ ] Jaki jest typowy retry rate?
  - **Stan:** NIEZNANY  -scraper liczy retries ale nie raportuje do backendu.
- [ ] Czy retry rate jest stabilny między runami?
  - **Stan:** NIEWERYFIKOWALNE.

### C2. Wskaźnik blokad
- [ ] Jaki jest blocked rate?
  - **Stan:** `AnalysisRunItem.scrape_status` rejestruje `blocked`, ale brak agregacji.
- [ ] Czy system reaguje na wzrost blocked rate?
  - **Stan:** NIE. Brak automatycznej reakcji.

### C3. Zachowanie w warunkach stresowych
- [ ] Co się dzieje przy słabszych IP?
  - **Stan:** Session reset → nowe proxy → retry. Ale brak scoring = losowy wybór kolejnego proxy.
- [ ] Co przy większym wolumenie (>1000 EAN)?
  - **Stan:** NIEPRZETESTOWANE w kontrolowany sposób. Sekwencyjne przetwarzanie = liniowy czas.
- [ ] Co przy przeciążeniu providera (Allegro)?
  - **Stan:** Retryable errors → requeue (max 3). Brak backoff, brak backpressure.

### C4. Mechanizmy ochronne
- [ ] Czy istnieje stop-loss?
  - **Stan:** BRAK. Run przetwarza wszystkie EAN niezależnie od error rate.
- [ ] Czy istnieje circuit breaker?
  - **Stan:** BRAK.
- [ ] Czy istnieje budget limit?
  - **Stan:** BRAK.

---

## D. Architektura i przepływy

### D1. Flow efektywności
- [ ] Czy flow UI → API → Celery → Scraper → DB jest optymalny?
  - **Stan:** FUNKCJONALNY, ale nieefektywny:
    - Celery task to sync loop (`for item in items`)
    - Scraper call = HTTP create + polling loop (blocking)
    - Brak batch submission
- [ ] Czy asynchroniczność Celery daje korzyści?
  - **Stan:** MINIMALNE przy `concurrency=1`. Główna korzyść: oddzielenie od HTTP request lifecycle.

### D2. Brakujące mechanizmy
- [ ] Cache requestów do scrapera
  - **Stan:** BRAK na poziomie HTTP. Tylko DB-level cache via `ProductMarketData` + TTL.
- [ ] Standardowy metering
  - **Stan:** BRAK.
- [ ] Kontrola równoległości per user
  - **Stan:** BRAK.
- [ ] Rate limiting na API
  - **Stan:** Tylko brute-force login (8/10min). Brak na `/upload`, `/run_from_db`.

---

## E. Dane i obserwowalność

### E1. Metryki per EAN
- [ ] `attempts` (ile prób proxy)  -**BRAK w DB**
- [ ] `latency_ms` (czas scrapowania)  -**BRAK w DB** (jest w AllegroResult ale nie persisted)
- [ ] `retries` (ile requeue'ów)  -**BRAK w DB** (jest w scraper Task ale nie zwracany w API)
- [ ] `captcha_solves`  -**BRAK w DB** (jest w AllegroResult ale nie persisted)

### E2. Metryki per run
- [ ] `cost`  -**BRAK**
- [ ] `throughput (EAN/min)`  -**BRAK** (obliczalny z `started_at`/`finished_at`/`processed_products` ale nie eksponowany)
- [ ] `error_rate`  -**BRAK** (obliczalny z `scrape_status` ale nie eksponowany)
- [ ] `retry_rate`  -**BRAK**
- [ ] `captcha_rate`  -**BRAK**
- [ ] `blocked_rate`  -**BRAK**

### E3. Observability
- [ ] Czy logi są strukturalne?
  - **Stan:** CZĘŚCIOWY. Python `logging` z ręcznym formatowaniem. Scraper ma custom Logger z activity tracking.
- [ ] Czy metryki są dostępne w API?
  - **Stan:** BRAK dedykowanego endpointu metryk.
  - Scraper `/health` zwraca Stats ale to dane globalne, nie per-run.
- [ ] Czy UI ma panel metryk?
  - **Stan:** BRAK. UI pokazuje progress (processed/total) ale nie metryki jakościowe.
- [ ] Czy istnieje eksport danych do analizy?
  - **Stan:** CZĘŚCIOWY. Excel export (`/download`) zawiera wyniki ale nie metryki scrapingu.

---

## Podsumowanie

| Obszar | Status | Priorytet |
|--------|--------|-----------|
| Metering kosztów | BRAK | KRYTYCZNY |
| Przepustowość EAN/min | NIEZMIERZONA | WYSOKI |
| Stop-loss / guardrails | BRAK | KRYTYCZNY |
| Retry/error rate tracking | BRAK W DB | WYSOKI |
| Proxy scoring/quarantine | BRAK | ŚREDNI |
| Rate limiting per user | BRAK | ŚREDNI |
| Kontrola concurrency | MINIMALNA | WYSOKI |
| Observability (API + UI) | BRAK | WYSOKI |
| Provider abstraction | BRAK | NISKI |
| Multi-tenant readiness | BRAK | PRZYSZŁY |
