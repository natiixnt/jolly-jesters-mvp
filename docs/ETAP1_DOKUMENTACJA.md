# Dokumentacja techniczna - Etap 1

**Projekt:** Jolly Jesters - Platforma analizy rynkowej e-commerce
**Etap:** 1 (MVP + warstwa produkcyjna)
**Budżet etapu:** 30 750,00 PLN brutto (ryczałt)
**Łączna liczba roboczogodzin:** 164 RBH
**Stawka przeliczeniowa:** 187,50 PLN/RBH
**Data zamknięcia:** 2026-04-08

---

## Spis treści

1. [Streszczenie wykonawcze](#1-streszczenie-wykonawcze)
2. [Realizacja zadań WBS](#2-realizacja-zadań-wbs)
3. [Metryki i metering (Bramka A)](#3-metryki-i-metering-bramka-a)
4. [Stabilność i bezpieczeństwo (Bramka B)](#4-stabilność-i-bezpieczeństwo-bramka-b)
5. [Bezpieczeństwo systemu](#5-bezpieczeństwo-systemu)
6. [Interfejs użytkownika](#6-interfejs-użytkownika)
7. [Testy](#7-testy)
8. [Parametry konfiguracyjne](#8-parametry-konfiguracyjne)
9. [Instrukcja uruchomienia](#9-instrukcja-uruchomienia)
10. [Kryteria odbioru - weryfikacja](#10-kryteria-odbioru---weryfikacja)
11. [Kosztorys Etapu 1 - rozliczenie roboczogodzin](#11-kosztorys-etapu-1---rozliczenie-roboczogodzin)

---

## 1. Streszczenie wykonawcze

Etap 1 obejmował zaprojektowanie i implementację kompletnej platformy do automatycznej analizy rynkowej produktów na marketplace Allegro. Platforma umożliwia import danych produktowych (pliki Excel/CSV lub API JSON), automatyczne pobieranie danych cenowych i sprzedażowych z Allegro, ocenę opłacalności według konfigurowalnych kryteriów oraz eksport wyników.

### Zakres dostarczonych prac

W ramach Etapu 1 zrealizowano następujące główne komponenty:

- **Moduł pozyskiwania danych** - automatyczne pobieranie cen i danych sprzedażowych z Allegro dla kodów EAN, z obsługą cache, retry i backpressure
- **System metryki kosztowej (Bramka A)** - metering kosztu operacji (koszt/1000 EAN, EAN/min, retry rate), formuła kosztowa, eksport metryk do CSV/Excel
- **Mechanizmy stabilności (Bramka B)** - stop-loss z 6 progami, profil równoległości 3x3, circuit breaker, warstwa dostępu sieciowego z auto-kwarantanną
- **Bezpieczeństwo** - autentykacja JWT i cookie-based, CSRF, rate limiting, walidacja danych wejściowych, audit logging, security headers, Docker security_opt
- **Interfejs użytkownika** - SPA z 13 zakładkami: dashboard, analiza, historia, rynek, warstwa sieciowa, ustawienia, monitoring, alerty, klucze API, konto, zużycie, administracja
- **Warstwa SaaS** - multi-tenant, billing, klucze API z zakresami (scopes), limity quotowe, powiadomienia webhook
- **153 testy automatyczne** - testy jednostkowe, integracyjne i bezpieczeństwa
- **Infrastruktura** - Docker Compose z 7 serwisami (backend, worker, scraper, PostgreSQL, Redis, nginx, cloudflared), migracje Alembic, CI/CD

### Mapowanie na wniosek grantowy

| Element wniosku | Realizacja |
|---|---|
| Moduł pozyskiwania danych rynkowych | `backend/app/workers/tasks.py`, `backend/app/providers/`, `backend/app/utils/allegro_scraper_client.py` |
| Analiza opłacalności | `backend/app/services/profitability_service.py` |
| System kosztowy z metrykami | `backend/app/services/analysis_service.py` (get_run_metrics) |
| Mechanizmy bezpieczeństwa operacyjnego | `backend/app/services/stoploss_service.py`, `backend/app/services/circuit_breaker.py` |
| Warstwa dostępu sieciowego | `backend/app/services/proxy_pool_service.py`, `backend/app/models/network_proxy.py` |
| Interfejs użytkownika | `backend/app/templates/index.html` (SPA), 16 routerów API |
| Testy i dokumentacja | `backend/app/tests/` (153 testy), niniejszy dokument |

---

## 2. Realizacja zadań WBS

### Zadanie 1.1.1 - Moduł pozyskiwania danych rynkowych

**Co zostało zrealizowane:**
Zaimplementowano kompletny moduł automatycznego pobierania danych cenowych i sprzedażowych z platformy Allegro dla produktów identyfikowanych kodem EAN. Moduł obsługuje różne tryby pracy: tryb live (pobieranie na żywo), tryb cached (analiza z bazy danych) oraz tryb bulk API (JSON).

**Pliki implementacji:**
- `backend/app/workers/tasks.py` - główny worker Celery przetwarzający analizy
- `backend/app/providers/base.py` - abstrakcyjna klasa bazowa providera (wzorzec Strategy)
- `backend/app/providers/allegro_scraper.py` - implementacja providera Allegro
- `backend/app/providers/registry.py` - rejestr providerów z dynamiczną inicjalizacją
- `backend/app/utils/allegro_scraper_client.py` - klient HTTP do komunikacji z modułem pobierania danych
- `backend/app/services/schemas.py` - schemat danych `AllegroResult`

**Opis techniczny:**
Worker Celery (`run_analysis_task`) pobiera listę pozycji (EAN) z tabeli `analysis_run_items`, a następnie dla każdej pozycji:
1. Sprawdza czy istnieją dane w cache (konfigurowalny TTL, domyślnie 30 dni)
2. Jeśli cache jest aktualny - używa danych z bazy (oszczędność kosztów)
3. Jeśli cache wygasł - pobiera dane przez provider Allegro
4. Zapisuje wynik w tabelach `product_market_data` i `product_effective_state`
5. Oblicza ocenę opłacalności według skonfigurowanych kryteriów
6. Zapisuje metryki (latencja, captcha, retry, koszt) na poziomie pozycji

Architektura providera jest oparta na wzorcu Strategy - abstrakcyjna klasa `ScraperProvider` definiuje interfejs `fetch(ean, run_id)`, a konkretne implementacje (aktualnie `AllegroScraperProvider`) są rejestrowane w `registry.py`. Umożliwia to łatwe dodanie nowych źródeł danych w przyszłości.

---

### Zadanie 1.2.1 - Model danych i migracje

**Co zostało zrealizowane:**
Zaprojektowano i zaimplementowano model danych obejmujący 18 tabel w PostgreSQL, z pełną historią migracji Alembic.

**Pliki implementacji:**
- `backend/app/models/` - katalog z 18 modelami SQLAlchemy
- `backend/alembic/versions/` - 16 plików migracji

**Tabele w systemie:**

| Tabela | Opis |
|---|---|
| `categories` | Kategorie produktów z konfiguracją prowizji i mnożnika opłacalności |
| `products` | Produkty z kodem EAN, cena zakupu, kategoria |
| `product_market_data` | Dane rynkowe: cena Allegro, liczba sprzedanych, źródło, payload |
| `product_effective_state` | Aktualny stan produktu: ostatnie dane, opłacalność |
| `analysis_runs` | Uruchomienia analizy: status, postęp, metadane, tryb |
| `analysis_run_items` | Pozycje analizy: EAN, cena, wynik, metryki (latency, captcha, retry) |
| `analysis_run_tasks` | Powiązanie z zadaniami Celery |
| `settings` | Konfiguracja systemu: cache TTL, progi stop-loss |
| `currency_rates` | Kursy walut (PLN, EUR, USD, CAD) |
| `network_proxies` | Pula dostępu sieciowego: URL, scoring, kwarantanna |
| `tenants` | Organizacje (multi-tenant) |
| `users` | Użytkownicy z hashowaniem haseł PBKDF2 |
| `usage_records` | Rekordy zużycia per tenant/okres |
| `monitored_eans` | EAN-y monitorowane cyklicznie |
| `alert_rules` | Reguły alertów (cena poniżej, spadek %) |
| `alert_events` | Zdarzenia alertowe |
| `notifications` | Powiadomienia systemowe |
| `api_keys` | Klucze API z zakresami i hashowaniem SHA-256 |

---

### Zadanie 1.2.2 - Serwis analizy opłacalności

**Co zostało zrealizowane:**
Zaimplementowano wielokryterialny algorytm oceny opłacalności produktu z konfigurowalnymi progami.

**Pliki implementacji:**
- `backend/app/services/profitability_service.py` - logika oceny opłacalności
- `backend/app/schemas/profitability.py` - schematy danych debug

**Opis techniczny:**
Algorytm oceny opłacalności uwzględnia 5 kryteriów (w kolejności priorytetu):

1. **Walidacja danych wejściowych** - czy cena zakupu > 0 i czy istnieje cena rynkowa
2. **Mnożnik opłacalności** - `przychod_netto / cena_zakupu >= mnoznik_kategorii` (domyślnie 1.5x)
3. **Minimalny zysk absolutny** - `przychod_netto - cena_zakupu >= PROFITABILITY_MIN_PROFIT_PLN` (domyślnie 15 PLN)
4. **Minimalny wolumen sprzedaży** - `sprzedane >= PROFITABILITY_MIN_SALES` (domyślnie 3 szt.)
5. **Maksymalna konkurencja** - `liczba_ofert <= PROFITABILITY_MAX_COMPETITION` (domyślnie 50)

Formuła przychodu netto:
```
przychod_netto = cena_allegro * (1 - stawka_prowizji_kategorii)
zysk = przychod_netto - cena_zakupu
mnoznik = przychod_netto / cena_zakupu
```

Wynik oceny przyjmuje jedną z trzech etykiet:
- `oplacalny` - wszystkie kryteria spełnione
- `nieoplacalny` - co najmniej jedno kryterium niespełnione
- `nieokreslony` - brak danych do oceny (brak ceny rynkowej lub cena zakupu <= 0)

Każdy wynik zawiera `reason_code` wskazujący na pierwsze niespełnione kryterium.

---

### Zadanie 1.2.3 - System metryki kosztowej (metering)

**Co zostało zrealizowane:**
Zaimplementowano system zbierania i raportowania metryk kosztowych na poziomie pojedynczej pozycji i całego runu analizy.

**Pliki implementacji:**
- `backend/app/models/analysis_run_item.py` - kolumny metryczne: `latency_ms`, `captcha_solves`, `retries`, `attempts`, `network_node_id`, `provider_status`
- `backend/app/services/analysis_service.py` - funkcja `get_run_metrics()` (linie 508-582)
- `backend/app/api/v1/analysis.py` - endpointy eksportu metryk (CSV, Excel)
- `backend/app/api/v1/metrics.py` - endpoint Prometheus-compatible `/api/v1/metrics/prometheus`

**Szczegółowy opis w sekcji 3.**

---

### Zadanie 1.3.1 - Import i eksport danych

**Co zostało zrealizowane:**
Zaimplementowano import danych z plików Excel/CSV z automatyczną konwersją walut oraz eksport wyników do formatów Excel i CSV.

**Pliki implementacji:**
- `backend/app/utils/excel_reader.py` - parser plików Excel/CSV z walidacją EAN i konwersją walut
- `backend/app/utils/excel_writer.py` - generator plików Excel z wynikami analizy
- `backend/app/services/import_service.py` - obsługa uploadu plików z sanityzacją nazw
- `backend/app/services/export_service.py` - eksport wyników analizy do pliku Excel

**Opis techniczny:**
Moduł importu obsługuje:
- Pliki `.xlsx`, `.xls`, `.csv` z automatyczną detekcją formatu (walidacja magic bytes)
- Automatyczne rozpoznanie kolumn (EAN, nazwa, cena, waluta) niezależnie od języka nagłówków
- Konwersję walut (EUR, USD, CAD -> PLN) według konfigurowalnych kursów
- Walidację kodów EAN (8-13 cyfr, suma kontrolna EAN-13)
- Deduplikację wierszy po kodzie EAN w ramach jednego uploadu
- Sanityzację nazw plików (ochrona przed path traversal)
- Limit uploadu: 50 MB z odczytem w chunkach (1 MB) - brak ryzyka OOM

---

### Zadanie 1.4.1 - Profil równoległości 3x3

**Co zostało zrealizowane:**
Zaimplementowano dwupoziomowy system kontroli równoległości: limit per użytkownik i limit globalny.

**Pliki implementacji:**
- `backend/app/api/v1/analysis.py` - funkcja `_check_concurrent_limit()` (linie 42-63)
- `backend/app/core/config.py` - parametry `concurrency_per_user`, `concurrency_global_max`, `max_concurrent_runs`
- `docker-compose.yml` - konfiguracja workera Celery z `--concurrency=${CELERY_CONCURRENCY:-3} --prefetch-multiplier=1`

**Opis techniczny:**
System kontroli równoległości działa na dwóch poziomach:

1. **Per-user** (`CONCURRENCY_PER_USER=3`): maksymalnie 3 równoczesne analizy na użytkownika
2. **Globalny** (`CONCURRENCY_GLOBAL_MAX=12`): maksymalnie 12 równoczesnych analiz w całym systemie
3. **Worker Celery** (`CELERY_CONCURRENCY=3`): maksymalnie 3 zadania równocześnie na workerze
4. **Prefetch multiplier** (`--prefetch-multiplier=1`): worker pobiera tylko 1 zadanie z kolejki na raz

Przy próbie uruchomienia analizy ponad limit, system zwraca HTTP 429 z czytelnym komunikatem. Blokada jest rozproszona - oparta na Redis (`_acquire_run_lock()`, `_release_run_lock()`) z automatycznym wygasaniem po 3600 sekund.

---

### Zadanie 1.5.1 - Warstwa dostępu sieciowego

**Co zostało zrealizowane:**
Zaimplementowano pełną warstwę zarządzania pulą dostępu sieciowego (proxy pool) z systemem scoringu, auto-kwarantanny i healthchecków.

**Pliki implementacji:**
- `backend/app/models/network_proxy.py` - model danych z polami: `url`, `url_hash`, `health_score`, `success_count`, `fail_count`, `quarantine_until`, `quarantine_reason`
- `backend/app/services/proxy_pool_service.py` - logika importu, scoringu, kwarantanny, healthchecków
- `backend/app/api/v1/proxy_pool.py` - REST API do zarządzania pula
- `backend/app/utils/validators.py` - walidacja URL proxy (schemat, host, port)

**Opis techniczny:**
Warstwa dostępu sieciowego obsługuje:

- **Import** - z pliku CSV/TXT (jeden URL na linie), z walidacją formatu URL, deduplikacja po `url_hash` (SHA-256)
- **Scoring** - każdy proxy ma `health_score` (0.0-1.0), modyfikowany przy sukcesie (+0.02) i błędzie (-0.05)
- **Auto-kwarantanna** - po 5 kolejnych błędach proxy jest automatycznie izolowany na `NETWORK_QUARANTINE_TTL` godzin (domyślnie 24h)
- **Ręczna kwarantanna** - API do izolowania/przywracania poszczególnych proxy
- **Healthcheck** - cykliczne sprawdzanie stanu proxy, automatyczne przywracanie po wygaśnięciu kwarantanny
- **Selekcja** - aktywne, niekwarantannowane proxy sortowane malejąco po `health_score`
- **Maskowanie URL** - w odpowiedziach API dane uwierzytelniające są zamaskowane (`***:***@host`)

Zestaw endpointów API:
- `GET /api/v1/proxy-pool` - lista proxy (z filtrem `active_only`, `include_quarantined`)
- `GET /api/v1/proxy-pool/health` - podsumowanie zdrowia puli
- `POST /api/v1/proxy-pool/import` - import listy proxy z pliku
- `POST /api/v1/proxy-pool/{id}/quarantine` - ręczna kwarantanna
- `DELETE /api/v1/proxy-pool/{id}/quarantine` - przywrócenie z kwarantanny

---

### Zadanie 1.6.1 - Mechanizm stop-loss

**Co zostało zrealizowane:**
Zaimplementowano mechanizm automatycznego zatrzymania analizy przy przekroczeniu progów jakościowych lub kosztowych.

**Pliki implementacji:**
- `backend/app/services/stoploss_service.py` - klasy `StopLossChecker`, `StopLossConfig`, `StopLossVerdict`
- `backend/app/workers/tasks.py` - integracja stop-loss z workerem (linie 464-501)
- `backend/app/models/setting.py` - persystentna konfiguracja progów
- `backend/app/models/enums.py` - status `ScrapeStatus.stopped_by_guardrail`

**Szczegółowy opis w sekcji 4.**

---

### Zadanie 1.7.1 - Interfejs użytkownika

**Co zostało zrealizowane:**
Zaimplementowano kompletny interfejs użytkownika jako Single Page Application (SPA) z 13 zakładkami, ciemnym/jasnym motywem i responsywnością mobilną.

**Pliki implementacji:**
- `backend/app/templates/index.html` - SPA (HTML/CSS/JavaScript), około 1000 linii
- `backend/app/templates/login.html` - strona logowania z CSRF
- `backend/app/api/v1/router.py` - 16 routerów API
- `backend/app/api/v1/` - 15 modułów endpointów

**Szczegółowy opis w sekcji 6.**

---

### Zadanie 1.8.1 - Testy i dokumentacja

**Co zostało zrealizowane:**
Zaimplementowano 153 testy automatyczne pokrywające logikę biznesową, bezpieczeństwo i integrację. Przygotowano dokumentację techniczną i narzędzia do testów wolumenowych.

**Pliki implementacji:**
- `backend/app/tests/` - 14 plików testowych
- `tools/volume_test.py` - protokół testu wolumenowego
- `docs/ETAP1_DOKUMENTACJA.md` - niniejszy dokument
- `Makefile` - automatyzacja uruchamiania testów

**Szczegółowy opis w sekcji 7.**

---

## 3. Metryki i metering (Bramka A)

### 3.1 Zbierane metryki

System zbiera metryki na dwóch poziomach:

#### Poziom pozycji (analysis_run_item)

Każda pozycja analizy (EAN) ma następujące pola metryczne w tabeli `analysis_run_items`:

| Pole | Typ | Opis |
|---|---|---|
| `latency_ms` | Integer | Czas odpowiedzi w milisekundach |
| `captcha_solves` | Integer | Liczba rozwiązanych CAPTCHA |
| `retries` | Integer | Liczba powtórzonych prób |
| `attempts` | Integer | Całkowita liczba prób |
| `network_node_id` | String(64) | Identyfikator węzła sieciowego |
| `provider_status` | String(32) | Status odpowiedzi providera |
| `scrape_status` | Enum | Status końcowy: ok, not_found, blocked, network_error, error, stopped_by_guardrail |

Pliki: `backend/app/models/analysis_run_item.py` (linie 36-42)

#### Poziom runu (agregowane)

Metryki agregowane obliczane przez `get_run_metrics()` w `backend/app/services/analysis_service.py`:

| Metryka | Opis | Formuła |
|---|---|---|
| `cost_per_1000_ean` | Szacowany koszt na 1000 EAN | `(captcha_cost + network_cost) / processed * 1000` |
| `ean_per_min` | Przepustowość (EAN na minutę) | `processed / (elapsed_seconds / 60)` |
| `success_rate` | Wskaźnik sukcesu | `completed / total` |
| `retry_rate` | Wskaźnik powtórzonych prób | `total_retries / processed` |
| `captcha_rate` | Wskaźnik CAPTCHA | `total_captcha / processed` |
| `blocked_rate` | Wskaźnik zablokowanych | `blocked / total` |
| `network_error_rate` | Wskaźnik błędów sieciowych | `network_error / total` |
| `avg_latency_ms` | Średnia latencja | `sum(latencies) / count(latencies)` |
| `p50_latency_ms` | Mediana latencji | Percentyl 50 |
| `p95_latency_ms` | P95 latencji | Percentyl 95 |
| `elapsed_seconds` | Czas trwania runu | `finished_at - started_at` |

### 3.2 Formuła kosztu

Formuła kosztu zaimplementowana w `backend/app/services/analysis_service.py` (linie 553-558):

```
gb_transfer_est = processed * 50KB / 1024 / 1024    # szacowane zużycie transferu
captcha_cost    = (total_captcha / 1000) * COST_RATE_ACCESS_VERIFICATION
network_cost    = gb_transfer_est * COST_RATE_NETWORK_PER_GB
total_cost      = captcha_cost + network_cost
cost_per_1000   = total_cost / processed * 1000
```

Gdzie:
- `COST_RATE_NETWORK_PER_GB` = 12.53 PLN/GB (domyślnie) - koszt transferu sieciowego
- `COST_RATE_ACCESS_VERIFICATION` = 5.19 PLN/1000 - koszt weryfikacji dostępu (CAPTCHA)
- Szacowany transfer na pozycję: 50 KB

### 3.3 Eksport metryk

Metryki można wyeksportować na trzy sposoby:

1. **API JSON** - `GET /api/v1/analysis/{run_id}/metrics` - pełny obiekt metryk w formacie JSON
2. **CSV** - `GET /api/v1/analysis/{run_id}/metrics/csv` - pobranie pliku CSV z metrykami
3. **Excel** - `GET /api/v1/analysis/{run_id}/metrics/excel` - pobranie pliku XLSX z metrykami (sformatówany arkusz z nazwami polskimi)

Dodatkowo metryki są dostępne w formacie Prometheus:
- `GET /api/v1/metrics/prometheus` - metryki zagregowane kompatybilne z Prometheus/Grafana

Metryki Prometheus obejmują:
- `jj_analysis_runs_total{status}` - liczba runów wg statusu
- `jj_active_runs` - aktywne runy
- `jj_eans_processed_total` - całkowita liczba przetworzonych EAN
- `jj_scrape_status_total{status}` - rozkład statusów
- `jj_captcha_solves_total` - suma rozwiązanych CAPTCHA
- `jj_avg_latency_ms` - średnia latencja
- `jj_ean_per_min_avg` - średnia przepustowość
- `jj_cost_per_1000_ean_avg` - średni koszt/1000 EAN
- `jj_stoploss_triggers_total` - suma wyzwoleń stop-loss
- `jj_proxy_total`, `jj_proxy_active`, `jj_proxy_quarantined` - stan puli proxy

### 3.4 Parametry konfiguracyjne meteringu

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `COST_RATE_NETWORK_PER_GB` | 12.53 | Koszt transferu sieciowego w PLN za 1 GB |
| `COST_RATE_ACCESS_VERIFICATION` | 5.19 | Koszt weryfikacji dostępu (CAPTCHA) w PLN za 1000 operacji |
| `CAPTCHA_COST_USD` | 0.002 | Koszt jednego rozwiązania CAPTCHA w USD (używany w billing) |

---

## 4. Stabilność i bezpieczeństwo (Bramka B)

### 4.1 Mechanizm stop-loss

Plik: `backend/app/services/stoploss_service.py`

Mechanizm stop-loss automatycznie zatrzymuje analizę gdy jakość pobierania danych spada poniżej akceptowalnych progów. Działa na zasadzie okna kroczącego (rolling window) o konfigurowalnej wielkości (domyślnie 20 ostatnich pozycji).

#### 6 progów stop-loss

| Próg | Parametr | Domyślna wartość | Opis |
|---|---|---|---|
| 1. Wskaźnik błędów | `stoploss_max_error_rate` | 0.50 (50%) | Maksymalny udział błędów w oknie |
| 2. Wskaźnik CAPTCHA | `stoploss_max_captcha_rate` | 0.80 (80%) | Maksymalny udział pozycji z CAPTCHA |
| 3. Kolejne błędy | `stoploss_max_consecutive_errors` | 10 | Maksymalna liczba kolejnych błędów |
| 4. Wskaźnik retry | `stoploss_max_retry_rate` | 0.05 (5%) | Maksymalny udział powtórzonych prób |
| 5. Wskaźnik blokad | `stoploss_max_blocked_rate` | 0.10 (10%) | Maksymalny udział zablokowanych pozycji |
| 6. Koszt/1000 | `stoploss_max_cost_per_1000` | 10.00 PLN | Maksymalny szacowany koszt na 1000 EAN |

#### Działanie

1. Każda przetworzona pozycja jest rejestrowana w oknie kroczącym (`deque(maxlen=window_size)`)
2. Najpierw sprawdzane są kolejne błędy (natychmiastowe wyzwolenie)
3. Progi rate-based są sprawdzane dopiero gdy okno jest pełne
4. Przy wyzwoleniu:
   - Analiza otrzymuje status `stopped`
   - Pole `run_metadata` zawiera `stop_reason`, `stop_details`, `stopped_at_item`
   - Nieprzetworzone pozycje otrzymują status `stopped_by_guardrail`
   - Wysyłany jest alert webhook (jeśli skonfigurowany)
   - Zapisywany jest wpis w logach audytu

Progi są konfigurowane przez interfejs użytkownika w zakładce "Ustawienia" i zapisywane w tabeli `settings`.

Integracja z workerem: `backend/app/workers/tasks.py` (linie 370-501)

### 4.2 Profil równoległości 3x3

Plik: `backend/app/api/v1/analysis.py`, `docker-compose.yml`

System kontroli równoległości działa na trzech warstwach:

| Warstwa | Parametr | Domyślnie | Opis |
|---|---|---|---|
| Per-user | `CONCURRENCY_PER_USER` | 3 | Maksymalna liczba równoczesnych analiz na użytkownika |
| Globalny | `CONCURRENCY_GLOBAL_MAX` | 12 | Globalny limit równoczesnych analiz |
| Worker | `CELERY_CONCURRENCY` | 3 | Równoległość workera Celery |
| Run lock | Redis `nx` | 1 per run | Rozproszona blokada Redis na poziomie runu (TTL 3600s) |

Dodatkowo:
- `--prefetch-multiplier=1` - worker pobiera 1 zadanie naraz (backpressure)
- `--max-tasks-per-child=20` - worker restartuje proces po 20 zadaniach (ochrona przed wyciekami pamięci)
- `acks_late=True` - potwierdzenie zadania dopiero po zakończeniu (odporność na awarię)

### 4.3 Circuit breaker

Plik: `backend/app/services/circuit_breaker.py`

Circuit breaker chroni system przed kaskadowym wywoływaniem błędnego serwisu. Implementuje wzorzec z trzema stanami:

| Stan | Opis |
|---|---|
| `closed` | Normalny - zapytania przepuszczane |
| `open` | Otwarty - zapytania natychmiast odrzucane (po `failure_threshold` błędów) |
| `half_open` | Pół-otwarty - przepuszczane jedno zapytanie testowe (po `recovery_timeout`) |

Parametry:
- `failure_threshold` = 10 błędów
- `recovery_timeout` = 60 sekund

Integracja: `backend/app/workers/tasks.py` linia 58: `_scraper_breaker = CircuitBreaker(name="scraper", failure_threshold=10, recovery_timeout=60)`

### 4.4 Warstwa dostępu sieciowego

Plik: `backend/app/services/proxy_pool_service.py`

System zarządzania pulą dostępu sieciowego z:

- **Import** - plik CSV/TXT, walidacja URL (schemat http/https/socks4/socks5, port 1-65535)
- **Health scoring** - `health_score` 0.0-1.0, decay -0.05 per błąd, recovery +0.02 per sukces
- **Auto-kwarantanna** - po 5 kolejnych błędach (`CONSECUTIVE_FAILS_QUARANTINE`), czas izolacji `NETWORK_QUARANTINE_TTL` (domyślnie 24h)
- **Healthcheck cykliczny** - co `NETWORK_HEALTHCHECK_INTERVAL` minut (domyślnie 5), automatyczne przywracanie po wygaśnięciu kwarantanny
- **Selekcja** - proxy sortowane malejąco po `health_score`, wykluczone kwarantannowane

### 4.5 Backpressure

System implementuje backpressure na kilku poziomach:

1. **Celery prefetch** - `--prefetch-multiplier=1` - worker pobiera tylko 1 zadanie
2. **Max pending tasks** - moduł pobierania danych ogranicza kolejkę (`MAX_PENDING_TASKS=100`)
3. **Per-run EAN cache** - powtarzające się kody EAN w jednym runie są obsługiwane z cache (bez dodatkowych zapytań)
4. **Redis run lock** - `set(f"run_lock:{run_id}", "1", nx=True, ex=3600)` - zapobieganie podwójnemu przetwarzaniu
5. **Request size limit** - middleware odrzuca zapytania > 50 MB
6. **Rate limiting** - slowapi z domyślnym limitem 200/min globalnie, 10/min na upload, 5/min na login

---

## 5. Bezpieczeństwo systemu

### 5.1 Autentykacja

#### Autentykacja UI (cookie-based)
Plik: `backend/app/main.py`

- Logowanie hasłem (`UI_PASSWORD`) przez formularz HTML
- Cookie sesyjne `jj_session` z podpisem HMAC-SHA256
- Konfigurowalny czas życia sesji (`UI_SESSION_TTL_HOURS`, domyślnie 24h)
- Flaga `httponly=True`, `samesite="strict"`, `secure` w produkcji
- Przy wylogowaniu usuwany cookie sesji i CSRF

#### Autentykacja API (JWT/HMAC)
Plik: `backend/app/services/auth_service.py`

- Tokeny HMAC-SHA256 z payloadem: `user_id:tenant_id:iat:jti:iss:aud`
- Weryfikacja issuer i audience
- Konfigurowalny TTL (`TOKEN_TTL_HOURS`, domyślnie 24h)
- Token refresh w ostatnich 25% czasu życia
- Generowanie losowego `JWT_SECRET` gdy nie ustawiony (z ostrzeżeniem), błąd w produkcji

#### Autentykacja API Key
Plik: `backend/app/services/api_key_service.py`

- Klucze z prefiksem `jj_` + 32 bajty URL-safe
- Przechowywanie jako SHA-256 hash
- Rate limiting per klucz (60 req/min)
- Zakresowa kontrola dostępu (scopes: `read`, `write`, `admin`)
- Automatyczna dezaktywacja po wygaśnięciu

### 5.2 Autoryzacja (multi-tenant, RBAC)

Plik: `backend/app/api/deps.py`

- Izolacja danych między tenantami - każdy run, alert, klucz API jest powiązany z `tenant_id`
- Weryfikacja dostępu do zasobów: `_verify_run_access()` sprawdza czy `run.tenant_id == current_user.tenant_id`
- Role użytkowników: `member`, `admin` - przechowywane w tabeli `users`
- Klucze API z zakresami: `read`, `write`, `admin`

### 5.3 Walidacja danych wejściowych

| Typ danych | Walidacja | Plik |
|---|---|---|
| Kod EAN | Regex `^\d{8,13}$` + suma kontrolna EAN-13 | `backend/app/utils/validators.py`, `backend/app/utils/ean.py` |
| Pliki | Magic bytes (XLSX: `PK\x03\x04`, XLS: OLE2), rozmiar <= 50 MB, chunked read | `backend/app/api/v1/analysis.py` (linie 120-129) |
| URL proxy | Schemat (http/https/socks4/socks5), hostname, port 1-65535 | `backend/app/utils/validators.py` |
| Ciągi znaków | Usuwanie znaków kontrolnych, limit długości (255 znaków) | `backend/app/utils/validators.py` (sanitize_string) |
| Nazwy plików | Sanityzacja: basename, usuwanie `.`, `\x00`, `/`, `\`, limit 200 znaków, ochrona przed path traversal | `backend/app/services/import_service.py` |
| UUID kategorii | `uuid.UUID(category_id)` z obsługą `ValueError` | `backend/app/api/v1/analysis.py` |
| Lista EAN (bulk) | Max 10000 elementów, walidacja każdego EAN | `backend/app/api/v1/analysis.py` (BulkEanRequest) |
| Kursy walut | Kod 3 litery, wartość > 0, PLN = 1.0, jedna domyślna | `backend/app/services/settings_service.py` |

### 5.4 Rate limiting (per-endpoint)

Plik: `backend/app/core/rate_limit.py`, `deploy/nginx.conf`

| Warstwa | Limit | Opis |
|---|---|---|
| Globalny (slowapi) | 200/min | Domyślny limit na wszystkie endpointy |
| Login (slowapi) | 5/min | Limit na formularz logowania |
| Upload (slowapi) | 10/min | Limit na upload plików |
| Import proxy (slowapi) | 5/min | Limit na import listy proxy |
| Login (nginx) | 10 req/min, burst 5 | Rate limiting na warstwie reverse proxy |
| API Key | 60/min per klucz | Limit per klucz API (in-memory) |

### 5.5 Ochrona przed atakami

| Atak | Ochrona | Lokalizacja |
|---|---|---|
| XSS | `escapeHtml()` w JavaScript, CSP header | `backend/app/templates/index.html`, `backend/app/main.py` (linia 84) |
| SQL Injection | Parameterized queries (SQLAlchemy ORM), brak raw SQL | Wszystkie serwisy |
| CSRF | Token w formularzu + cookie, weryfikacja `hmac.compare_digest` | `backend/app/main.py` (linie 239-280) |
| Brute Force (login) | 5 prób / 10 min per IP, blokada z `Retry-After` | `backend/app/main.py` (linie 176-187) |
| Brute Force (konto) | Exponential backoff: 5min, 10min, ..., max 1h | `backend/app/services/auth_service.py` (linie 49-57) |
| Path Traversal | `filepath.resolve().is_relative_to(target_dir.resolve())` | `backend/app/services/import_service.py` (linia 45) |
| File Upload | Magic bytes validation, chunked read, size limit, filename sanitization | `backend/app/api/v1/analysis.py` |
| Timing Attack | `hmac.compare_digest()` wszędzie | `backend/app/main.py`, `backend/app/services/auth_service.py` |
| DoS (OOM) | Chunked file read (1 MB), emergency dict cleanup > 10000 entries | `backend/app/main.py`, `backend/app/api/v1/analysis.py` |
| Information Leakage | Stack trace nie wyciekają - generic error message | `backend/app/main.py` (linie 49-57), OpenAPI/Swagger wyłączone |

### 5.6 Audit logging

Plik: `backend/app/services/audit_service.py`

System logowania zdarzeń bezpieczeństwa. Każde zdarzenie zawiera:
- `timestamp` (UTC ISO 8601)
- `action` (typ zdarzenia)
- `user_id`, `tenant_id`, `ip`
- `details` (dane kontekstowe)

Logowane zdarzenia:
- `login_success`, `login_failure` - próby logowania
- `file_upload` - upload pliku analizy
- `run_cancel` - anulowanie analizy
- `settings_update` - zmiana ustawień
- `currency_rates_update` - zmiana kursów walut
- `proxy_import` - import listy proxy
- `stoploss_trigger` - wyzwolenie mechanizmu stop-loss
- `api_key_create`, `api_key_revoke` - operacje na kluczach API

### 5.7 Security headers

#### Warstwa aplikacji (FastAPI middleware)
Plik: `backend/app/main.py` (linie 78-93)

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline';
  style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
  font-src 'self' data: https://fonts.gstatic.com;
  connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'
```

#### Warstwa nginx
Plik: `deploy/nginx.conf`

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
Content-Security-Policy: frame-ancestors 'none'
server_tokens off
```

### 5.8 Hardening infrastruktury

#### Docker
Plik: `docker-compose.yml`

- `security_opt: [no-new-privileges:true]` - backend i worker nie mogą eskalować uprawnień
- `stop_signal: SIGTERM`, `stop_grace_period: 30s` - graceful shutdown workera
- Healthchecki na wszystkich serwisach (postgres, redis, backend, worker, scraper)
- Dedykowane sieci Docker (izolacja)
- Wolumeny tylko gdzie potrzebne (dane/workspace)

#### Nginx
Plik: `deploy/nginx.conf`

- `server_tokens off` - ukrycie wersji nginx
- `client_max_body_size 50m` - limit uploadu
- `limit_req_zone` - rate limiting na /login (10 req/min)
- Ograniczenie metod HTTP (`GET|HEAD|POST|PUT|PATCH|DELETE`)
- Proxy headers (`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`)
- `proxy_read_timeout 300s` - timeout dla długich analiz

#### Hasła i sekrety
- Wymog silnego `UI_PASSWORD` w produkcji (RuntimeError jeśli słabe)
- Wymog `JWT_SECRET` w produkcji
- Losowe generowanie sekretów w środowisku dev (z ostrzeżeniem)
- Hasła hashowane PBKDF2-SHA256 z unikalnym soleniem (100 000 iteracji)

---

## 6. Interfejs użytkownika

### 6.1 Architektura UI

Interfejs jest zaimplementowany jako Single Page Application (SPA) w czystym HTML/CSS/JavaScript, bez zewnętrznych frameworków (brak React/Vue/Angular). Cały interfejs jest zawarty w jednym pliku: `backend/app/templates/index.html`.

Cechy interfejsu:
- **Motyw ciemny/jasny** - przełącznik w stopce bocznej
- **Responsywność** - mobilne menu hamburger, adaptacyjne siatki
- **Brak zewnętrznych zależności** - tylko font Inter z Google Fonts
- **Komunikacja z API** - `fetch()` z cookie-based autentykacją

### 6.2 Zakładki interfejsu

#### Dashboard (tab-dashboard)
Główny panel z kartami metryk:
- Aktywne analizy (liczba)
- Status modułu pobierania danych (ikona + tekst)
- Donut chart: rozkład opłacalności produktów (opłacalny/nieopłacalny/nieokreślony)
- Donut chart: rozkład statusów pobierania danych (ok/not_found/error/blocked)
- Stan warstwy dostępu sieciowego (liczba aktywnych/kwarantannowanych)
- Średnia przepustowość (EAN/min) i koszt/1000 EAN

#### Nowa analiza (tab-new-run)
- Upload pliku Excel/CSV z wyborem kategorii
- Wybór trybu: live (pobieranie na żywo) lub cached (z bazy)
- Konfiguracja filtrów: cache days, limit, źródło, EAN contains
- Alternatywa: start analizy z bazy danych (bez uploadu)

#### Historia (tab-history)
- Lista uruchomionych analiz z statusem, postępem, datą
- Możliwość pobrania wyników (Excel) i podglądu metryk

#### Panel metryk runu
Po kliknięciu w run, wyświetlane są szczegółowe metryki:
- Koszt (cost_per_1000_ean)
- Przepustowość (ean_per_min)
- Latencja (avg, p50, p95)
- Wskaźniki: success_rate, retry_rate, captcha_rate, blocked_rate, network_error_rate
- Czas trwania
- Eksport do CSV i Excel

#### Dane rynkowe (tab-market)
- Przeglądanie danych rynkowych produktów
- Filtrowanie po kategorii, statusie, EAN

#### Warstwa dostępu sieciowego (tab-proxies)
- Lista proxy z health score, liczba sukcesów/błędów
- Import z pliku (drag & drop)
- Ręczna kwarantanna/przywracanie
- Podsumowanie zdrowia puli (total, active, quarantined, avg_health_score)

#### Ustawienia (tab-settings)
- Cache TTL (dni)
- Progi stop-loss (6 parametrów)
- Włączenie/wyłączenie stop-loss
- Rozmiar okna kroczącego
- Kursy walut (PLN, EUR, USD, CAD)

#### Pomoc (tab-help)
- Dokumentacja użytkownika
- Opis formatów plików
- Przykłady

#### Monitoring EAN (tab-monitoring)
- Dodawanie EAN do monitoringu cyklicznego
- Konfiguracja interwału odświeżania (minuty)
- Priorytet monitoringu
- Lista monitorowanych EAN z datami ostatniego/następnego sprawdzenia

#### Alerty (tab-alerts)
- Tworzenie reguł alertowych (cena poniżej/powyżej, spadek %, brak w sprzedaży)
- Historia wyzwolonych alertów
- Powiadomienia webhook

#### Klucze API (tab-api-keys)
- Tworzenie kluczy z zakresami (read, write, admin)
- Lista kluczy (prefix, zakresy, ostatnie użycie)
- Odwoływanie kluczy

#### Konto (tab-account)
- Rejestracja i logowanie użytkowników
- Dane konta i sesji

#### Zużycie (tab-usage)
- Zużycie bieżącego okresu (EAN, CAPTCHA, koszt)
- Limit quota i procent użycia
- Historia zużycia per miesiąc

#### Administracja (tab-admin)
- Zarządzanie tenantami (tworzenie, konfiguracja quota)

### 6.3 Raport przyczyny stop-loss

Gdy analiza jest zatrzymana przez mechanizm stop-loss, interfejs wyświetla:
- Komunikat o przyczynie zatrzymania (`stop_reason`)
- Szczegóły (np. `error_rate: 0.55, threshold: 0.50, window: 20`)
- Numer pozycji przy której nastąpiło zatrzymanie (`stopped_at_item`)
- Możliwość pobrania częściowych wyników

Dane są dostępne z `run_metadata` obiektu analizy oraz przez SSE event `stopped`.

### 6.4 Eksport wyników

- **Excel (.xlsx)** - `GET /api/v1/analysis/{run_id}/download` - pełny raport z wynikami analizy, opłacalnością, cenami
- **CSV** - `GET /api/v1/analysis/{run_id}/metrics/csv` - metryki runu
- **Excel metryk** - `GET /api/v1/analysis/{run_id}/metrics/excel` - sformatówany arkusz z metrykami

---

## 7. Testy

### 7.1 Podsumowanie

| Metryka | Wartość |
|---|---|
| Całkowita liczba testów | 153 |
| Pliki testowe | 14 |
| Czas uruchomienia | < 5 sekund |
| Framework | pytest |

### 7.2 Pliki testowe i pokrycie

| Plik | Liczba testów (est.) | Zakres |
|---|---|---|
| `test_security_hardening.py` | 20+ | Hashowanie haseł, walidacja tokenów, brute force, timing attacks |
| `test_security.py` | 15+ | CSRF, sesje, cookie security, auth bypass |
| `test_stoploss.py` | 15+ | 6 progów stop-loss, okno kroczące, konfiguracja |
| `test_circuit_breaker.py` | 10+ | Stany circuit breaker, recovery, failure threshold |
| `test_validators.py` | 10+ | Walidacja EAN, proxy URL, sanityzacja |
| `test_profitability_service.py` | 10+ | Algorytm opłacalności, różne scenariusze |
| `test_allegro_scraper_client.py` | 10+ | Klient HTTP, obsługa błędów, timeout |
| `test_integration.py` | 15+ | Endpointy API, upload, analiza, eksport |
| `test_excel_reader.py` | 10+ | Import Excel/CSV, konwersja walut |
| `test_excel_writer.py` | 5+ | Generowanie plików Excel |
| `test_analysis_result_serialization.py` | 5+ | Serializacja wyników analizy |
| `test_worker_cache_policy.py` | 10+ | Polityka cache workera |
| `test_worker_not_found_cache.py` | 5+ | Cache dla pozycji not_found |
| `test_critical_coverage.py` | 10+ | Krytyczne ścieżki kodu |
| `test_no_api_remnants.py` | 3+ | Weryfikacja braku nieużywanych API |
| `test_auth_service.py` | 10+ | Autentykacja, tokeny, refresh |

### 7.3 Kategorie testów

- **Testy jednostkowe** - izolowane testy logiki biznesowej (profitability, stoploss, circuit breaker, validators)
- **Testy integracyjne** - testy endpointów API z bazą danych (upload, analiza, eksport)
- **Testy bezpieczeństwa** - CSRF, brute force, timing attacks, path traversal, XSS, password security

### 7.4 Uruchamianie testów

```bash
# Uruchomienie wszystkich testow
make test

# Rownowazne polecenie
UI_AUTH_BYPASS=1 PYTHONPATH=backend python -m pytest -q

# Uruchomienie pojedynczego pliku
UI_AUTH_BYPASS=1 PYTHONPATH=backend python -m pytest backend/app/tests/test_stoploss.py -v
```

### 7.5 Protokół testu wolumenowego

Plik: `tools/volume_test.py`

Narzędzie do automatycznego testu wolumenowego, które:

1. Wgrywa plik z produktami (Excel/CSV)
2. Monitoruje postęp analizy w czasie rzeczywistym
3. Pobiera metryki po zakończeniu
4. Generuje raport w formacie tekstowym z weryfikacją kryteriów odbioru

Użycie:
```bash
# Uruchomienie testu
make volume-test

# Lub reczne
python tools/volume_test.py --url http://localhost --file sample.xlsx --output raport_test_wolumenowy.txt
```

Raport zawiera:
- Metryki kluczowe (Bramka A): koszt/1000 EAN, EAN/min
- Metryki stabilności (Bramka B): success rate, CAPTCHA rate, retry rate, blocked rate
- Szczegóły: liczba produktów, błędów, nie znalezionych, zablokowanych, latencja
- Kryteria odbioru: PASS/FAIL dla każdego kryterium

---

## 8. Parametry konfiguracyjne

Plik: `backend/.env.example`

### Baza danych

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `DB_URL` | `postgresql+psycopg2://mvp:mvp@postgres:5432/mvpdb` | Connection string do PostgreSQL |

### Redis / Celery

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | URL do serwera Redis |
| `CELERY_BROKER_URL` | `${REDIS_URL}` | URL brokera Celery |
| `CELERY_RESULT_BACKEND` | `${REDIS_URL}` | URL backendu wyników Celery |

### Bezpieczeństwo

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `JWT_SECRET` | (wymagane w produkcji) | Sekret do podpisywania tokenów JWT |
| `UI_PASSWORD` | (wymagane w produkcji) | Hasło do interfejsu użytkownika |
| `UI_BASIC_AUTH_USER` | `admin` | Użytkownik HTTP Basic Auth (legacy) |
| `UI_BASIC_AUTH_PASSWORD` | (wymagane) | Hasło HTTP Basic Auth (legacy) |
| `UI_SESSION_TTL_HOURS` | `24` | Czas życia sesji w godzinach |
| `COOKIE_SECURE` | `false` | Flaga Secure na cookies (automatycznie true w produkcji) |
| `CORS_ORIGINS` | `http://localhost,http://localhost:80` | Dozwolone originy CORS |
| `REGISTRATION_KEY` | (opcjonalne) | Klucz rejestracyjny dla nowych użytkowników |

### Profil równoległości 3x3

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `CONCURRENCY_PER_USER` | `3` | Max równoczesnych analiz per użytkownik |
| `CONCURRENCY_GLOBAL_MAX` | `12` | Globalny max równoczesnych analiz |
| `MAX_CONCURRENT_RUNS` | `3` | Max równoczesnych runów |
| `CELERY_CONCURRENCY` | `3` | Równoległość workera Celery |

### Mechanizm stop-loss

Progi konfigurowane przez interfejs użytkownika, wartości domyślne:

| Parametr (w tabeli settings) | Domyślna wartość | Opis |
|---|---|---|
| `stoploss_enabled` | `true` | Włączenie mechanizmu |
| `stoploss_window_size` | `20` | Rozmiar okna kroczącego |
| `stoploss_max_error_rate` | `0.50` | Max wskaźnik błędów |
| `stoploss_max_captcha_rate` | `0.80` | Max wskaźnik CAPTCHA |
| `stoploss_max_consecutive_errors` | `10` | Max kolejnych błędów |
| `stoploss_max_retry_rate` | `0.05` | Max wskaźnik retry |
| `stoploss_max_blocked_rate` | `0.10` | Max wskaźnik blokad |
| `stoploss_max_cost_per_1000` | `10.00` | Max koszt/1000 EAN (PLN) |

### Metering kosztu

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `COST_RATE_NETWORK_PER_GB` | `12.53` | Koszt transferu sieciowego (PLN/GB) |
| `COST_RATE_ACCESS_VERIFICATION` | `5.19` | Koszt weryfikacji dostępu (PLN/1000) |
| `CAPTCHA_COST_USD` | `0.002` | Koszt CAPTCHA (USD/szt.) |

### Warstwa dostępu sieciowego

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `NETWORK_HEALTHCHECK_INTERVAL` | `5` | Interwal healthchecków (minuty) |
| `NETWORK_QUARANTINE_TTL` | `24` | Czas kwarantanny (godziny) |
| `SCRAPER_PROXIES_FILE` | `/workspace/data/proxies.txt` | Ścieżka do pliku z listą proxy |

### Moduł pobierania danych

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `ALLEGRO_SCRAPER_URL` | `http://allegro_scraper:3000` | URL modułu pobierania danych |
| `ALLEGRO_SCRAPER_POLL_INTERVAL` | `2.0` | Interwal odpytywania (sekundy) |
| `ALLEGRO_SCRAPER_TIMEOUT_SECONDS` | `90` | Timeout zapytania (sekundy) |
| `SCRAPER_WORKER_COUNT` | `3` | Liczba workerów modułu |
| `SCRAPER_CONCURRENCY_PER_WORKER` | `3` | Równoległość per worker |
| `SCRAPER_MAX_TASK_RETRIES` | `2` | Max powtórzonych prób |
| `SCRAPER_MAX_PENDING_TASKS` | `100` | Max oczekujących zadań |
| `ANYSOLVER_API_KEY` | (wymagane) | Klucz API do weryfikacji dostępu |

### Analiza opłacalności

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `PROFITABILITY_MIN_PROFIT_PLN` | `5.0` | Minimalny zysk absolutny (PLN) |
| `PROFITABILITY_MIN_SALES` | `10` | Minimalna liczba sprzedaży |
| `PROFITABILITY_MAX_COMPETITION` | `50` | Maksymalna liczba ofert konkurencji |
| `EUR_TO_PLN_RATE` | `4.5` | Domyślny kurs EUR/PLN |

### Środowisko

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `ENVIRONMENT` | `dev` | Środowisko (dev/production) |
| `WORKSPACE` | `/workspace` | Katalog roboczy |
| `LOG_FORMAT` | `text` | Format logów (text/json) |
| `LOG_LEVEL` | `INFO` | Poziom logowania |

### Alerty i powiadomienia

| Parametr | Domyślna wartość | Opis |
|---|---|---|
| `ALERT_WEBHOOK_URL` | (opcjonalne) | URL webhooka alertów |
| `NOTIFICATION_WEBHOOK_URL` | (opcjonalne) | URL webhooka powiadomień |
| `TUNNEL_TOKEN` | (opcjonalne) | Token Cloudflare Tunnel |

---

## 9. Instrukcja uruchomienia

### 9.1 Docker Compose (pełny stack - produkcja)

**Wymagania:**
- Docker >= 20.10
- Docker Compose >= 2.0
- Minimum 4 GB RAM
- Minimum 10 GB przestrzeni dyskowej

**Uruchomienie:**

```bash
# 1. Sklonowanie repozytorium
git clone <repo-url>
cd jolly-jesters-mvp

# 2. Konfiguracja srodowiska
cp backend/.env.example backend/.env
# Edytuj backend/.env - ustaw silne hasla:
#   UI_PASSWORD=<silne_haslo>
#   JWT_SECRET=<min_32_znaki>
#   ANYSOLVER_API_KEY=<klucz>

# 3. Uruchomienie
make up
# lub
docker compose up --build

# 4. Dostep do interfejsu
# Otworz przegladarke: http://localhost
# Zaloguj sie haslem ustawionym w UI_PASSWORD
```

**Serwisy Docker Compose:**

| Serwis | Port | Opis |
|---|---|---|
| `nginx` | 80 | Reverse proxy, terminacja SSL |
| `backend` | 8000 (wewn.) | FastAPI - REST API + SPA |
| `worker` | - | Celery worker - przetwarzanie analiz |
| `allegro_scraper` | 3000 (wewn.) | Moduł pobierania danych |
| `postgres` | 5432 (wewn.) | Baza danych PostgreSQL 15 |
| `redis` | 6379 (wewn.) | Broker wiadomości i cache |
| `cloudflared` | - | Tunel Cloudflare (opcjonalnie) |

**Kolejność uruchomienia:**
1. `postgres` i `redis` (healthcheck)
2. `migrations` (czeka na postgres, uruchamia `alembic upgrade head`)
3. `allegro_scraper` (healthcheck)
4. `backend` i `worker` (czekają na migracje + scraper + postgres + redis)
5. `nginx` (czeka na backend)
6. `cloudflared` (czeka na nginx)

### 9.2 Lokalne uruchomienie (development)

```bash
# 1. Srodowisko Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# 2. Baza danych (lokalna)
# Uruchom PostgreSQL i Redis lokalnie lub przez Docker
docker compose up postgres redis -d

# 3. Migracje
cd backend && alembic upgrade head && cd ..

# 4. Backend
PYTHONPATH=backend uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 5. Worker (osobny terminal)
PYTHONPATH=backend celery -A app.workers.tasks worker --loglevel=info -Q analysis --concurrency=3

# 6. Testy
make test
```

### 9.3 Wymagania systemowe

| Komponent | Minimum | Zalecane |
|---|---|---|
| CPU | 2 rdzenie | 4 rdzenie |
| RAM | 4 GB | 8 GB |
| Dysk | 10 GB | 50 GB |
| System operacyjny | Linux/macOS/Windows (Docker) | Ubuntu 22.04 LTS |
| Python | 3.9+ | 3.11+ |
| PostgreSQL | 14+ | 15 |
| Redis | 6+ | 7 |
| Docker | 20.10+ | 24+ |
| Docker Compose | 2.0+ | 2.24+ |

---

## 10. Kryteria odbioru - weryfikacja

### Kryterium 10.1: Moduł pozyskiwania danych pobiera dane cenowe z Allegro

**Weryfikacja:**
1. Uruchom system: `make up`
2. Zaloguj się do interfejsu: http://localhost
3. Utwórz kategorie w zakładce "Dane rynkowe"
4. Wgraj plik Excel z kodami EAN w zakładce "Nowa analiza"
5. Obserwuj postęp analizy - pozycje powinny przechodzić ze statusu `pending` -> `in_progress` -> `ok`/`not_found`
6. Zweryfikuj: `GET /api/v1/analysis/{run_id}/results` - pole `allegro_price_pln` i `sold_count` wypełnione

**Dowód:** Pole `scrape_status=ok` i niepuste `allegro_price` w odpowiedzi API.

---

### Kryterium 10.2: System oblicza koszt/1000 EAN i EAN/min

**Weryfikacja:**
1. Uruchom analizę (jak w 10.1)
2. Po zakończeniu: `GET /api/v1/analysis/{run_id}/metrics`
3. Sprawdź pola `cost_per_1000_ean` i `ean_per_min` - powinny być liczbami > 0
4. Eksportuj: `GET /api/v1/analysis/{run_id}/metrics/csv` - pobrany plik CSV z metrykami
5. Sprawdź metryki Prometheus: `GET /api/v1/metrics/prometheus` - linie `jj_cost_per_1000_ean_avg` i `jj_ean_per_min_avg`

**Dowód:** `cost_per_1000_ean` i `ean_per_min` jako wartości liczbowe w odpowiedzi metrics.

---

### Kryterium 10.3: Mechanizm stop-loss zatrzymuje analizę przy przekroczeniu progów

**Weryfikacja:**
1. W zakładce "Ustawienia", ustaw niski próg: `max_error_rate = 0.01` (1%)
2. Uruchom analizę z dużym plikiem
3. Gdy przynajmniej 1 pozycja z 20 zakończy się błędem, analiza powinna się zatrzymać
4. Sprawdź: `GET /api/v1/analysis/{run_id}` - `status=stopped`, `run_metadata.stop_reason=error_rate`
5. W interfejsie widoczny komunikat o przyczynie zatrzymania

**Dowód:** `status=stopped` i `run_metadata.stop_reason` w odpowiedzi API.

---

### Kryterium 10.4: Profil równoległości 3x3 ogranicza liczbę równoczesnych analiz

**Weryfikacja:**
1. Ustaw `CONCURRENCY_PER_USER=2` w `.env`
2. Uruchom 2 analizy jednocześnie - obie powinny się uruchomić
3. Uruchom 3. analizę - powinna zostać odrzucona z HTTP 429
4. Komunikat: "Limit równoczesnych analiz na użytkownika (2) osiągnięty."

**Dowód:** HTTP 429 z komunikatem błędu przy przekroczeniu limitu.

---

### Kryterium 10.5: Warstwa dostępu sieciowego z auto-kwarantanną

**Weryfikacja:**
1. W zakładce "Warstwa sieciowa" importuj plik z listą proxy
2. Sprawdź: `GET /api/v1/proxy-pool` - lista proxy z `health_score=1.0`
3. Sprawdź: `GET /api/v1/proxy-pool/health` - podsumowanie puli
4. Symuluj awarię: `POST /api/v1/proxy-pool/{id}/quarantine` - proxy przechodzi do kwarantanny
5. Sprawdź: `GET /api/v1/proxy-pool?include_quarantined=true` - proxy ma `quarantine_until` i `quarantine_reason`
6. Przywróć: `DELETE /api/v1/proxy-pool/{id}/quarantine`

**Dowód:** Pola `quarantine_until` i `health_score` w odpowiedzi API.

---

### Kryterium 10.6: Circuit breaker chroni przed kaskadowymi błędami

**Weryfikacja:**
1. Sprawdź w logach workera: `CIRCUIT_BREAKER scraper OPEN after 10 failures` po 10 kolejnych błędach modułu pobierania danych
2. Kolejne pozycje otrzymują status `error` z komunikatem `circuit_breaker_open` (bez obciążania modułu)
3. Po `recovery_timeout` (60s): `CIRCUIT_BREAKER scraper half-open (trying recovery)`
4. Jeśli następne zapytanie jest sukcesem: `CIRCUIT_BREAKER scraper recovered`

**Dowód:** Logi workera oraz `error_message=circuit_breaker_open` w pozycjach analizy.

---

### Kryterium 10.7: System zapewnia bezpieczeństwo autentykacji

**Weryfikacja:**
1. Wejdź na http://localhost - przekierowanie na /login
2. Podaj błędne hasło 5 razy - blokada 10 minut (HTTP 429)
3. Zaloguj się poprawnie - cookie `jj_session` z `httponly`, `samesite=strict`
4. Wejdź na API bez cookie: `curl http://localhost/api/v1/analysis` - HTTP 401
5. Sprawdź audit log: `AUDIT: login_failure` i `AUDIT: login_success`

**Dowód:** HTTP 429 po 5 próbach, HTTP 401 bez autentykacji, wpisy w logach audytu.

---

### Kryterium 10.8: Security headers są aktywne

**Weryfikacja:**
```bash
curl -I http://localhost/
```
Oczekiwane nagłówki:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'self'; ...`
- Brak nagłówka `Server: nginx/x.x.x` (ukryty)

**Dowód:** Nagłówki w odpowiedzi HTTP.

---

### Kryterium 10.9: Eksport wyników do Excel i CSV

**Weryfikacja:**
1. Uruchom analizę i poczekaj na zakończenie
2. Pobierz wyniki: `GET /api/v1/analysis/{run_id}/download` - plik `.xlsx`
3. Pobierz metryki CSV: `GET /api/v1/analysis/{run_id}/metrics/csv` - plik `.csv`
4. Pobierz metryki Excel: `GET /api/v1/analysis/{run_id}/metrics/excel` - plik `.xlsx`
5. Otwórz pliki - zweryfikuj zawartość (EAN, ceny, opłacalność, metryki)

**Dowód:** Poprawne pliki Excel/CSV z danymi analizy.

---

### Kryterium 10.10: 153 testy automatyczne przechodzą

**Weryfikacja:**
```bash
make test
```

Oczekiwany wynik:
```
153 passed in X.XXs
```

**Dowód:** Wynik uruchomienia `make test` z 153 testami.

---

### Kryterium 10.11: Protokół testu wolumenowego działa

**Weryfikacja:**
```bash
make volume-test
```

Lub ręcznie:
```bash
python tools/volume_test.py --url http://localhost --file sample.xlsx --output raport.txt
```

Oczekiwany wynik: raport z sekcjami "METRYKI KLUCZOWE", "METRYKI STABILNOŚCI", "KRYTERIA ODBIORU" i weryfikacja PASS/FAIL.

**Dowód:** Wygenerowany raport z wynikami testu wolumenowego.

---

### Kryterium 10.12: System działa w kontenerach Docker

**Weryfikacja:**
```bash
docker compose up --build
docker compose ps
```

Wszystkie 7 serwisów powinny mieć status `healthy` lub `running`:
- `postgres` (healthy)
- `redis` (healthy)
- `allegro_scraper` (healthy)
- `backend` (healthy)
- `worker` (healthy)
- `nginx` (running)
- `cloudflared` (running)

**Dowód:** `docker compose ps` z wszystkimi serwisami w stanie zdrowym.

---

## Podsumowanie

Etap 1 projektu Jolly Jesters został zrealizowany w pełnym zakresie, obejmując:

- **Moduł pozyskiwania danych** z cache, retry, backpressure i provider abstraction
- **System metryki kosztowej** z formułą kosztu i eksportem do CSV/Excel/Prometheus
- **6 mechanizmów stabilności** - stop-loss, 3x3, circuit breaker, proxy pool, backpressure, healthcheck
- **Kompleksowe bezpieczeństwo** - JWT, CSRF, rate limiting, audit logging, security headers, Docker hardening
- **Interfejs użytkownika** z 13 zakładkami, ciemnym/jasnym motywem i responsywnością
- **Warstwa SaaS** - multi-tenant, billing, API keys z zakresami, monitoring cykliczny, alerty
- **153 testy automatyczne** pokrywające logikę biznesową, integrację i bezpieczeństwo
- **Infrastruktura produkcyjna** - Docker Compose z 7 serwisami, nginx, migracje, CI/CD

System jest gotowy do produkcyjnego wdrożenia i spełnia wszystkie kryteria odbioru zdefiniowane w specyfikacji Etapu 1.

---

## 11. Kosztorys Etapu 1 - rozliczenie roboczogodzin

Rozliczenie Etapu 1 ma charakter ryczałtowy. Poniższe rozbicie stanowi kosztorys informacyjny.

- **Kwota ryczałtowa:** 30 750,00 PLN brutto
- **Łączna liczba roboczogodzin:** 164 RBH
- **Stawka przeliczeniowa (brutto):** 187,50 PLN/RBH

| ID | Zadanie | Role (RBH) | RBH | Koszt brutto (PLN) |
|----|---------|------------|-----|---------------------|
| 1.1.1 | Analiza kodu i projekt metryk | PM 4h; Backend 4h | 8 | 1 500,00 |
| 1.1.2 | Migracje DB: run_metrics, item_metrics | Backend 6h | 6 | 1 125,00 |
| 1.2.1 | Instrumentacja worker: liczniki attempt/retry | Backend 10h; QA 3h | 13 | 2 437,50 |
| 1.2.2 | Instrumentacja worker: weryfikacja dostępu/blocked/latency | Backend 8h; Moduł danych 4h; QA 2h | 14 | 2 625,00 |
| 1.2.3 | Agregacja metryk runu | Backend 6h; QA 2h | 8 | 1 500,00 |
| 1.3.1 | API: /runs/{id}/metrics | Backend 6h; QA 2h | 8 | 1 500,00 |
| 1.3.2 | UI: panel metryk + eksport CSV/Excel | Frontend 8h; Backend 6h; QA 2h | 16 | 3 000,00 |
| 1.4.1 | Profil 3x3: semafory i limitery | Backend 8h; DevOps 4h; QA 4h | 16 | 3 000,00 |
| 1.4.2 | Backpressure i stabilizacja | Backend 6h; Moduł danych 4h; QA 2h | 12 | 2 250,00 |
| 1.5.1 | Warstwa dostępu sieciowego - import CSV + walidacja | Backend 6h; QA 2h | 8 | 1 500,00 |
| 1.5.2 | Healthcheck + scoring + auto-kwarantanna | Backend 2h; Moduł danych 8h; QA 2h | 12 | 2 250,00 |
| 1.6.1 | Mechanizm stop-loss: rolling window + progi | Backend 8h; QA 3h | 11 | 2 062,50 |
| 1.6.2 | UI: raport przyczyny mechanizmu stop-loss | Frontend 4h; Backend 2h; QA 1h | 7 | 1 312,50 |
| 1.7.1 | Naprawa kontraktów cache/offline | Backend 4h; Frontend 6h; QA 2h | 12 | 2 250,00 |
| 1.8.1 | Test wolumenowy + protokół odbioru | QA 6h; PM 4h; DevOps 3h | 13 | 2 437,50 |
| | **SUMA** | | **164** | **30 750,00** |

### Struktura kosztów według ról

| Rola | Roboczogodziny | Udział |
|------|---------------|--------|
| Backend developer | 78h | 47,6% |
| Frontend developer | 18h | 11,0% |
| Moduł pozyskiwania danych | 16h | 9,8% |
| QA / Tester | 27h | 16,5% |
| DevOps | 11h | 6,7% |
| PM / Analityk | 14h | 8,5% |
| **Razem** | **164h** | **100%** |

### Uwagi do kosztorysu

1. Stawka 187,50 PLN/RBH (brutto) obejmuje pełny koszt realizacji włącznie z narzutami.
2. Rozliczenie ma charakter ryczałtowy - podane rozbicie służy celom informacyjnym.
3. Prace obejmowały projektowanie, implementację, testy, code review i dokumentację.
4. Bezpieczeństwo systemu (autentykacja, walidacja, rate limiting, audit logging) zostało zrealizowane jako element każdego zadania, nie jako odrębna pozycja kosztowa.
