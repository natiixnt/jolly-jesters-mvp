# Roadmapa Wdrożenia  - Jolly Jesters MVP

Data: 2026-03-18
Wersja: 1.0

---

## Faza 1: Metering (ETA: 1-2 dni)

**Cel:** Zbieranie granularnych metryk per EAN i per run. Podstawa pod wszystkie kolejne fazy.

### 1.1 Model danych  - nowe kolumny `AnalysisRunItem`
- **Plik:** `backend/app/models/analysis_run_item.py`
- **Zmiany:** Dodanie kolumn `latency_ms` (Integer), `captcha_solves` (Integer), `retries` (Integer), `attempts` (Integer)
- **Migracja:** Nowy plik Alembic `20260318_add_metering_columns.py`
- **Zależności:** Brak

### 1.2 Scraper  - eksponowanie metryk w API
- **Pliki:**
  - `allegro.pl-scraper-main/src/types.ts`  - dodanie `retries` do `TaskResponse`
  - `allegro.pl-scraper-main/src/api/routes.ts`  - dodanie `retries` i `proxyAttempts` do response
  - `allegro.pl-scraper-main/src/worker/worker.ts`  - zapisanie `proxyAttempts` w task result
  - `allegro.pl-scraper-main/src/scraper/allegro.ts`  - dodanie `proxyAttempts` do `AllegroFetchResult`
- **Zależności:** Brak (niezależne od backendu)

### 1.3 Backend  - przechwytywanie metryk
- **Pliki:**
  - `backend/app/services/schemas.py`  - dodanie `retries`, `attempts` do `AllegroResult`
  - `backend/app/utils/allegro_scraper_client.py`  - ekstrakcja `retries` i `proxyAttempts` z response
  - `backend/app/workers/tasks.py`  - persystencja `latency_ms`, `captcha_solves`, `retries`, `attempts` w `_apply_result()`
- **Zależności:** 1.1 (model), 1.2 (scraper changes)

### 1.4 API metryk per run
- **Pliki:**
  - `backend/app/schemas/analysis.py`  - nowy schema `AnalysisRunMetrics`
  - `backend/app/services/analysis_service.py`  - nowa funkcja `get_run_metrics()`
  - `backend/app/api/v1/analysis.py`  - nowy endpoint `GET /{run_id}/metrics`
- **Metryki:**
  - `avg_latency_ms`, `p50_latency_ms`, `p95_latency_ms`
  - `total_captcha_solves`, `total_retries`
  - `retry_rate`, `captcha_rate`, `blocked_rate`, `network_error_rate`
  - `ean_per_min`, `cost_per_1000_ean` (estymowany z CAPTCHA cost)
  - `elapsed_seconds`
- **Zależności:** 1.1, 1.3

```
Diagram zależności Fazy 1:

  1.1 Model ──────┐
                   ├──→ 1.3 Backend capture ──→ 1.4 Metrics API
  1.2 Scraper API ─┘
```

---

## Faza 2: Stop-Loss (ETA: 1-2 dni)

**Cel:** Automatyczne zatrzymanie runa przy przekroczeniu progów jakościowych. Redukcja ryzyka kosztowego.

### 2.1 Nowy status `stopped`
- **Plik:** `backend/app/models/enums.py`  - dodanie `stopped = "stopped"` do `AnalysisStatus`
- **Migracja:** `ALTER TYPE analysisstatus ADD VALUE IF NOT EXISTS 'stopped'`
- **Zależności:** Brak

### 2.2 Konfiguracja stop-loss
- **Pliki:**
  - `backend/app/models/setting.py`  - nowe kolumny:
    - `stoploss_enabled` (Boolean, default True)
    - `stoploss_window_size` (Integer, default 20)
    - `stoploss_max_error_rate` (Numeric, default 0.50)
    - `stoploss_max_captcha_rate` (Numeric, default 0.80)
    - `stoploss_max_consecutive_errors` (Integer, default 10)
  - `backend/app/schemas/settings.py`  - update `SettingsRead`/`SettingsUpdate`
  - `backend/app/services/settings_service.py`  - update `get_settings()`/`update_settings()`
- **Migracja:** Nowy plik Alembic `20260318_add_stoploss.py`
- **Zależności:** 2.1

### 2.3 StopLossChecker service
- **Nowy plik:** `backend/app/services/stoploss_service.py`
- **Logika:**
  - Rolling window (deque) o konfigurowalnym rozmiarze
  - Sprawdzenia: error_rate, captcha_rate, consecutive_errors
  - Zwraca `StopLossVerdict` z `should_stop`, `reason`, `details`
- **Zależności:** 2.2 (konfiguracja)

### 2.4 Integracja z Celery task
- **Plik:** `backend/app/workers/tasks.py`
- **Zmiany:**
  - Przed pętlą: załadowanie konfiguracji, utworzenie `StopLossChecker`
  - Po `_apply_result()`: wywołanie `stoploss.record()`, sprawdzenie verdict
  - Przy stop: ustawienie `status=stopped`, zapis `stop_reason` w `run_metadata`, `break`
  - Terminal check: dodanie `AnalysisStatus.stopped` do warunków końcowych
- **Zależności:** Faza 1 (metering fields), 2.3 (checker)

### 2.5 SSE stream + API updates
- **Plik:** `backend/app/api/v1/analysis.py`
- **Zmiany:**
  - `stream_analysis()`: dodanie `stopped` do terminal statuses, emit "stopped" event
  - `cancel_analysis_run()`: dodanie `stopped` do finished statuses
  - Download: dodanie `stopped` do allowed statuses
- **Zależności:** 2.1, 2.4

```
Diagram zależności Fazy 2:

  Faza 1 (metering) ──→ 2.4 Integracja Celery
                              ↑
  2.1 Status ─→ 2.2 Config ─→ 2.3 Checker
                                    ↓
                              2.5 SSE + API
```

---

## Faza 3: Network Pool (ETA: 2-3 dni)

**Cel:** Zarządzanie jakością proxy, redukcja kosztów CAPTCHA i ban rate.

### 3.1 Model `NetworkProxy`
- Nowy model w `backend/app/models/network_proxy.py`
- Kolumny: `url`, `label`, `is_active`, `success_count`, `fail_count`, `last_success_at`, `last_fail_at`, `quarantine_until`, `health_score`

### 3.2 Redis-based proxy state
- Sorted sets dla real-time scoring
- TTL keys dla quarantine (`proxy:{id}:quarantine`)
- Persistence: sync Redis ↔ PostgreSQL co N minut

### 3.3 Proxy API endpoints
- `POST /api/v1/proxies/import`  - CSV import, upsert do `network_proxies`
- `GET /api/v1/proxies/health`  - dashboard zdrowia proxy
- `PATCH /api/v1/proxies/{id}/quarantine`  - ręczna kwarantanna

### 3.4 Scraper proxy reporting
- Rozszerzenie task result o metryki proxy (proxy_url hash, success/fail, latency)
- Backend aktualizuje scoring po każdym task result

### 3.5 Integracja z worker
- Scraper wybiera proxy na podstawie health_score (weighted random zamiast round-robin)
- Auto-quarantine przy N kolejnych failach z tego samego IP

---

## Faza 4: Kontrolowane Skalowanie 3x3 (ETA: 2-3 dni)

**Cel:** Przejście z `concurrency=1` do kontrolowanego skalowania.

### 4.1 Scraper scaling
- Zmiana env: `WORKER_COUNT=3`, `CONCURRENCY_PER_WORKER=3` (9 concurrent scrapes)
- Istniejący WorkerPool i Worker już to obsługują

### 4.2 Celery batch submission
- Zamiast sekwencyjnego `for item in items` → batch submit do scrapera
- Async polling wielu tasków jednocześnie
- Lub: Celery `concurrency=3` z osobnym procesem per run

### 4.3 Fair-share queuing
- Dodanie `runId` do scraper Task
- Round-robin dequeuing w TaskQueue (zamiast FIFO)
- Zapobieganie starving mniejszych runów przez duże

### 4.4 Backpressure
- Queue depth limit w scraperze
- `429 Too Many Requests` gdy `pending > threshold`
- Exponential backoff w backend client

### 4.5 Limity per user
- Max concurrent runs per user (np. 3)
- Sprawdzenie w `/upload` i `/run_from_db` przed enqueue

---

## Faza 5: Provider Abstraction (ETA: 1 dzień)

**Cel:** Standaryzacja interfejsu providerów, przygotowanie pod multi-marketplace.

### 5.1 Provider interface
- Nowy `backend/app/providers/base.py`  - abstrakcyjne `ScraperProvider`
- Metody: `fetch(ean) -> AllegroResult`, `health() -> dict`

### 5.2 Allegro provider
- Nowy `backend/app/providers/allegro_scraper.py`
- Wrapper na istniejący `allegro_scraper_client.py`

### 5.3 Provider registry
- Factory pattern z config-driven selection
- `PROVIDER_MODE` env variable
- Health check aggregacja

---

## Faza 6: Observability + UI (ETA: 2-3 dni)

**Cel:** Widoczność metryk w UI, eksport danych.

### 6.1 UI panel metryk
- Dashboard z metrykami bieżącego runa (live via SSE)
- Historia runów z metrykami jakościowymi

### 6.2 Eksport metryk
- Rozszerzenie Excel export o kolumny metryczne
- CSV export metryk run-level

### 6.3 Alerty (przygotowanie)
- Webhook na stop-loss events
- Konfiguracja progów alertów

---

## Faza 7: SaaS Readiness (przyszłość)

### 7.1 Multi-tenant
- User model, tenant isolation
- Per-tenant settings i limity

### 7.2 Billing
- Metering → billing pipeline
- Quota management

### 7.3 CI/CD
- Staging/production environments
- Automated testing pipeline
- Blue-green deployment

### 7.4 Monitoring
- Prometheus/Grafana integration
- Alerting rules
- SLA dashboard

---

## Podsumowanie harmonogramu

| Faza | Zakres | ETA | Zależności |
|------|--------|-----|-----------|
| 1. Metering | Model + scraper + API | 1-2 dni | Brak |
| 2. Stop-Loss | Config + checker + Celery | 1-2 dni | Faza 1 |
| 3. Network Pool | Model + Redis + scoring | 2-3 dni | Faza 1 |
| 4. Skalowanie 3x3 | Celery + scraper + limits | 2-3 dni | Faza 1, 2, 3 |
| 5. Provider | Abstraction + registry | 1 dzień | Brak (równolegle z 1-3) |
| 6. Observability | UI + export + alerty | 2-3 dni | Faza 1, 2 |
| 7. SaaS | Multi-tenant + billing | TBD | Faza 1-6 |

**Łączny czas Faz 1-5:** ~7-11 dni roboczych
**Priorytet:** Faza 1 → Faza 2 → Faza 3 → Faza 4 (Faza 5 równolegle)
