# Dokumentacja techniczna - Etap 1

**Projekt:** Jolly Jesters - Platforma analizy rynkowej e-commerce
**Etap:** 1 (MVP + warstwa produkcyjna)
**Budzet etapu:** 30 750 PLN
**Data zamkniecia:** 2026-04-08

---

## Spis tresci

1. [Streszczenie wykonawcze](#1-streszczenie-wykonawcze)
2. [Realizacja zadan WBS](#2-realizacja-zadan-wbs)
3. [Metryki i metering (Bramka A)](#3-metryki-i-metering-bramka-a)
4. [Stabilnosc i bezpieczenstwo (Bramka B)](#4-stabilnosc-i-bezpieczenstwo-bramka-b)
5. [Bezpieczenstwo systemu](#5-bezpieczenstwo-systemu)
6. [Interfejs uzytkownika](#6-interfejs-uzytkownika)
7. [Testy](#7-testy)
8. [Parametry konfiguracyjne](#8-parametry-konfiguracyjne)
9. [Instrukcja uruchomienia](#9-instrukcja-uruchomienia)
10. [Kryteria odbioru - weryfikacja](#10-kryteria-odbioru---weryfikacja)

---

## 1. Streszczenie wykonawcze

Etap 1 obejmowal zaprojektowanie i implementacje kompletnej platformy do automatycznej analizy rynkowej produktow na marketplace Allegro. Platforma umozliwia import danych produktowych (pliki Excel/CSV lub API JSON), automatyczne pobieranie danych cenowych i sprzedazowych z Allegro, ocene oplacalnosci wedlug konfigurowalnych kryteriow oraz eksport wynikow.

### Zakres dostarczonych prac

W ramach Etapu 1 zrealizowano nastepujace glowne komponenty:

- **Modul pozyskiwania danych** - automatyczne pobieranie cen i danych sprzedazowych z Allegro dla kodow EAN, z obsluga cache, retry i backpressure
- **System metryki kosztowej (Bramka A)** - metering kosztu operacji (koszt/1000 EAN, EAN/min, retry rate), formula kosztowa, eksport metryk do CSV/Excel
- **Mechanizmy stabilnosci (Bramka B)** - stop-loss z 6 progami, profil rownoleglosci 3x3, circuit breaker, warstwa dostepu sieciowego z auto-kwarantanna
- **Bezpieczenstwo** - autentykacja JWT i cookie-based, CSRF, rate limiting, walidacja danych wejsciowych, audit logging, security headers, Docker security_opt
- **Interfejs uzytkownika** - SPA z 13 zakladkami: dashboard, analiza, historia, rynek, warstwa sieciowa, ustawienia, monitoring, alerty, klucze API, konto, zuzycie, administracja
- **Warstwa SaaS** - multi-tenant, billing, klucze API z zakresami (scopes), limity quotowe, powiadomienia webhook
- **153 testy automatyczne** - testy jednostkowe, integracyjne i bezpieczenstwa
- **Infrastruktura** - Docker Compose z 7 serwisami (backend, worker, scraper, PostgreSQL, Redis, nginx, cloudflared), migracje Alembic, CI/CD

### Mapowanie na wniosek grantowy

| Element wniosku | Realizacja |
|---|---|
| Modul pozyskiwania danych rynkowych | `backend/app/workers/tasks.py`, `backend/app/providers/`, `backend/app/utils/allegro_scraper_client.py` |
| Analiza oplacalnosci | `backend/app/services/profitability_service.py` |
| System kosztowy z metrykami | `backend/app/services/analysis_service.py` (get_run_metrics) |
| Mechanizmy bezpieczenstwa operacyjnego | `backend/app/services/stoploss_service.py`, `backend/app/services/circuit_breaker.py` |
| Warstwa dostepu sieciowego | `backend/app/services/proxy_pool_service.py`, `backend/app/models/network_proxy.py` |
| Interfejs uzytkownika | `backend/app/templates/index.html` (SPA), 16 routerow API |
| Testy i dokumentacja | `backend/app/tests/` (153 testy), niniejszy dokument |

---

## 2. Realizacja zadan WBS

### Zadanie 1.1.1 - Modul pozyskiwania danych rynkowych

**Co zostalo zrealizowane:**
Zaimplementowano kompletny modul automatycznego pobierania danych cenowych i sprzedazowych z platformy Allegro dla produktow identyfikowanych kodem EAN. Modul obsluguje rozne tryby pracy: tryb live (pobieranie na zywo), tryb cached (analiza z bazy danych) oraz tryb bulk API (JSON).

**Pliki implementacji:**
- `backend/app/workers/tasks.py` - glowny worker Celery przetwarzajacy analizy
- `backend/app/providers/base.py` - abstrakcyjna klasa bazowa providera (wzorzec Strategy)
- `backend/app/providers/allegro_scraper.py` - implementacja providera Allegro
- `backend/app/providers/registry.py` - rejestr providerow z dynamiczna inicjalizacja
- `backend/app/utils/allegro_scraper_client.py` - klient HTTP do komunikacji z modulem pobierania danych
- `backend/app/services/schemas.py` - schemat danych `AllegroResult`

**Opis techniczny:**
Worker Celery (`run_analysis_task`) pobiera liste pozycji (EAN) z tabeli `analysis_run_items`, a nastepnie dla kazdej pozycji:
1. Sprawdza czy istnieja dane w cache (konfigurowalny TTL, domyslnie 30 dni)
2. Jesli cache jest aktualny - uzywa danych z bazy (oszczednosc kosztow)
3. Jesli cache wygasl - pobiera dane przez provider Allegro
4. Zapisuje wynik w tabelach `product_market_data` i `product_effective_state`
5. Oblicza ocene oplacalnosci wedlug skonfigurowanych kryteriow
6. Zapisuje metryki (latencja, captcha, retry, koszt) na poziomie pozycji

Architektura providera jest oparta na wzorcu Strategy - abstrakcyjna klasa `ScraperProvider` definiuje interfejs `fetch(ean, run_id)`, a konkretne implementacje (aktualnie `AllegroScraperProvider`) sa rejestrowane w `registry.py`. Umozliwia to latwe dodanie nowych zrodel danych w przyszlosci.

---

### Zadanie 1.2.1 - Model danych i migracje

**Co zostalo zrealizowane:**
Zaprojektowano i zaimplementowano model danych obejmujacy 18 tabel w PostgreSQL, z pelna historia migracjami Alembic.

**Pliki implementacji:**
- `backend/app/models/` - katalog z 18 modelami SQLAlchemy
- `backend/alembic/versions/` - 16 plikow migracji

**Tabele w systemie:**

| Tabela | Opis |
|---|---|
| `categories` | Kategorie produktow z konfiguracja prowizji i mnoznika oplacalnosci |
| `products` | Produkty z kodem EAN, cena zakupu, kategoria |
| `product_market_data` | Dane rynkowe: cena Allegro, liczba sprzedanych, zrodlo, payload |
| `product_effective_state` | Aktualny stan produktu: ostatnie dane, oplacalnosc |
| `analysis_runs` | Uruchomienia analizy: status, postep, metadane, tryb |
| `analysis_run_items` | Pozycje analizy: EAN, cena, wynik, metryki (latency, captcha, retry) |
| `analysis_run_tasks` | Powiazanie z zadaniami Celery |
| `settings` | Konfiguracja systemu: cache TTL, progi stop-loss |
| `currency_rates` | Kursy walut (PLN, EUR, USD, CAD) |
| `network_proxies` | Pula dostepu sieciowego: URL, scoring, kwarantanna |
| `tenants` | Organizacje (multi-tenant) |
| `users` | Uzytkownicy z hashowaniem hasel PBKDF2 |
| `usage_records` | Rekordy zuzycia per tenant/okres |
| `monitored_eans` | EAN-y monitorowane cyklicznie |
| `alert_rules` | Reguly alertow (cena ponizej, spadek %) |
| `alert_events` | Zdarzenia alertowe |
| `notifications` | Powiadomienia systemowe |
| `api_keys` | Klucze API z zakresami i hashowaniem SHA-256 |

---

### Zadanie 1.2.2 - Serwis analizy oplacalnosci

**Co zostalo zrealizowane:**
Zaimplementowano wielokryterialny algorytm oceny oplacalnosci produktu z konfigurowalnymi progami.

**Pliki implementacji:**
- `backend/app/services/profitability_service.py` - logika oceny oplacalnosci
- `backend/app/schemas/profitability.py` - schematy danych debug

**Opis techniczny:**
Algorytm oceny oplacalnosci uwzglednia 5 kryteriow (w kolejnosci priorytetu):

1. **Walidacja danych wejsciowych** - czy cena zakupu > 0 i czy istnieje cena rynkowa
2. **Mnoznik oplacalnosci** - `przychod_netto / cena_zakupu >= mnoznik_kategorii` (domyslnie 1.5x)
3. **Minimalny zysk absolutny** - `przychod_netto - cena_zakupu >= PROFITABILITY_MIN_PROFIT_PLN` (domyslnie 15 PLN)
4. **Minimalny wolumen sprzedazy** - `sprzedane >= PROFITABILITY_MIN_SALES` (domyslnie 3 szt.)
5. **Maksymalna konkurencja** - `liczba_ofert <= PROFITABILITY_MAX_COMPETITION` (domyslnie 50)

Formula przychodu netto:
```
przychod_netto = cena_allegro * (1 - stawka_prowizji_kategorii)
zysk = przychod_netto - cena_zakupu
mnoznik = przychod_netto / cena_zakupu
```

Wynik oceny przyjmuje jedna z trzech etykiet:
- `oplacalny` - wszystkie kryteria spelnione
- `nieoplacalny` - co najmniej jedno kryterium niespelnione
- `nieokreslony` - brak danych do oceny (brak ceny rynkowej lub cena zakupu <= 0)

Kazdy wynik zawiera `reason_code` wskazujacy na pierwsze niespelnione kryterium.

---

### Zadanie 1.2.3 - System metryki kosztowej (metering)

**Co zostalo zrealizowane:**
Zaimplementowano system zbierania i raportowania metryk kosztowych na poziomie pojedynczej pozycji i calego runu analizy.

**Pliki implementacji:**
- `backend/app/models/analysis_run_item.py` - kolumny metryczne: `latency_ms`, `captcha_solves`, `retries`, `attempts`, `network_node_id`, `provider_status`
- `backend/app/services/analysis_service.py` - funkcja `get_run_metrics()` (linie 508-582)
- `backend/app/api/v1/analysis.py` - endpointy eksportu metryk (CSV, Excel)
- `backend/app/api/v1/metrics.py` - endpoint Prometheus-compatible `/api/v1/metrics/prometheus`

**Szczegolowy opis w sekcji 3.**

---

### Zadanie 1.3.1 - Import i eksport danych

**Co zostalo zrealizowane:**
Zaimplementowano import danych z plikow Excel/CSV z automatyczna konwersja walut oraz eksport wynikow do formatow Excel i CSV.

**Pliki implementacji:**
- `backend/app/utils/excel_reader.py` - parser plikow Excel/CSV z walidacja EAN i konwersja walut
- `backend/app/utils/excel_writer.py` - generator plikow Excel z wynikami analizy
- `backend/app/services/import_service.py` - obsluga uploadu plikow z sanityzacja nazw
- `backend/app/services/export_service.py` - eksport wynikow analizy do pliku Excel

**Opis techniczny:**
Modul importu obsluguje:
- Pliki `.xlsx`, `.xls`, `.csv` z automatyczna detekcja formatu (walidacja magic bytes)
- Automatyczne rozpoznanie kolumn (EAN, nazwa, cena, waluta) niezaleznie od jezyka naglowkow
- Konwersje walut (EUR, USD, CAD -> PLN) wedlug konfigurowalnych kursow
- Walidacje kodow EAN (8-13 cyfr, suma kontrolna EAN-13)
- Deduplikacje wierszy po kodzie EAN w ramach jednego uploadu
- Sanityzacje nazw plikow (ochrona przed path traversal)
- Limit uploadu: 50 MB z odczytem w chunkach (1 MB) - brak ryzyka OOM

---

### Zadanie 1.4.1 - Profil rownoleglosci 3x3

**Co zostalo zrealizowane:**
Zaimplementowano dwupoziomowy system kontroli rownoleglosci: limit per uzytkownik i limit globalny.

**Pliki implementacji:**
- `backend/app/api/v1/analysis.py` - funkcja `_check_concurrent_limit()` (linie 42-63)
- `backend/app/core/config.py` - parametry `concurrency_per_user`, `concurrency_global_max`, `max_concurrent_runs`
- `docker-compose.yml` - konfiguracja workera Celery z `--concurrency=${CELERY_CONCURRENCY:-3} --prefetch-multiplier=1`

**Opis techniczny:**
System kontroli rownoleglosci dziala na dwoch poziomach:

1. **Per-user** (`CONCURRENCY_PER_USER=3`): maksymalnie 3 rownoczesne analizy na uzytkownika
2. **Globalny** (`CONCURRENCY_GLOBAL_MAX=12`): maksymalnie 12 rownoczesnych analiz w calym systemie
3. **Worker Celery** (`CELERY_CONCURRENCY=3`): maksymalnie 3 zadania rownoczesnie na workerze
4. **Prefetch multiplier** (`--prefetch-multiplier=1`): worker pobiera tylko 1 zadanie z kolejki na raz

Przy probie uruchomienia analizy ponad limit, system zwraca HTTP 429 z czytelnym komunikatem. Blokada jest rozproszona - oparta na Redis (`_acquire_run_lock()`, `_release_run_lock()`) z automatycznym wygasaniem po 3600 sekund.

---

### Zadanie 1.5.1 - Warstwa dostepu sieciowego

**Co zostalo zrealizowane:**
Zaimplementowano pelna warstwe zarzadzania pula dostepu sieciowego (proxy pool) z systemem scoringu, auto-kwarantanny i healthcheckow.

**Pliki implementacji:**
- `backend/app/models/network_proxy.py` - model danych z polami: `url`, `url_hash`, `health_score`, `success_count`, `fail_count`, `quarantine_until`, `quarantine_reason`
- `backend/app/services/proxy_pool_service.py` - logika importu, scoringu, kwarantanny, healthcheckow
- `backend/app/api/v1/proxy_pool.py` - REST API do zarzadzania pula
- `backend/app/utils/validators.py` - walidacja URL proxy (schemat, host, port)

**Opis techniczny:**
Warstwa dostepu sieciowego obsluguje:

- **Import** - z pliku CSV/TXT (jeden URL na linie), z walidacja formatu URL, deduplikacja po `url_hash` (SHA-256)
- **Scoring** - kazdy proxy ma `health_score` (0.0-1.0), modyfikowany przy sukcesie (+0.02) i bledzie (-0.05)
- **Auto-kwarantanna** - po 5 kolejnych bledach proxy jest automatycznie izolowany na `NETWORK_QUARANTINE_TTL` godzin (domyslnie 24h)
- **Reczna kwarantanna** - API do izolowania/przywracania poszczegolnych proxy
- **Healthcheck** - cykliczne sprawdzanie stanu proxy, automatyczne przywracanie po wygasnieciu kwarantanny
- **Selekcja** - aktywne, niekwarantannowane proxy sortowane malejaco po `health_score`
- **Maskowanie URL** - w odpowiedziach API dane uwierzytelniajace sa zamaskowane (`***:***@host`)

Zestaw endpointow API:
- `GET /api/v1/proxy-pool` - lista proxy (z filtrem `active_only`, `include_quarantined`)
- `GET /api/v1/proxy-pool/health` - podsumowanie zdrowia puli
- `POST /api/v1/proxy-pool/import` - import listy proxy z pliku
- `POST /api/v1/proxy-pool/{id}/quarantine` - reczna kwarantanna
- `DELETE /api/v1/proxy-pool/{id}/quarantine` - przywrocenie z kwarantanny

---

### Zadanie 1.6.1 - Mechanizm stop-loss

**Co zostalo zrealizowane:**
Zaimplementowano mechanizm automatycznego zatrzymania analizy przy przekroczeniu progow jakosciowych lub kosztowych.

**Pliki implementacji:**
- `backend/app/services/stoploss_service.py` - klasy `StopLossChecker`, `StopLossConfig`, `StopLossVerdict`
- `backend/app/workers/tasks.py` - integracja stop-loss z workerem (linie 464-501)
- `backend/app/models/setting.py` - persystentna konfiguracja progow
- `backend/app/models/enums.py` - status `ScrapeStatus.stopped_by_guardrail`

**Szczegolowy opis w sekcji 4.**

---

### Zadanie 1.7.1 - Interfejs uzytkownika

**Co zostalo zrealizowane:**
Zaimplementowano kompletny interfejs uzytkownika jako Single Page Application (SPA) z 13 zakladkami, ciemnym/jasnym motywem i responsywnoscia mobilna.

**Pliki implementacji:**
- `backend/app/templates/index.html` - SPA (HTML/CSS/JavaScript), okolo 1000 linii
- `backend/app/templates/login.html` - strona logowania z CSRF
- `backend/app/api/v1/router.py` - 16 routerow API
- `backend/app/api/v1/` - 15 modulow endpointow

**Szczegolowy opis w sekcji 6.**

---

### Zadanie 1.8.1 - Testy i dokumentacja

**Co zostalo zrealizowane:**
Zaimplementowano 153 testy automatyczne pokrywajace logike biznesowa, bezpieczenstwo i integracje. Przygotowano dokumentacje techniczna i narzedzia do testow wolumenowych.

**Pliki implementacji:**
- `backend/app/tests/` - 14 plikow testowych
- `tools/volume_test.py` - protokol testu wolumenowego
- `docs/ETAP1_DOKUMENTACJA.md` - niniejszy dokument
- `Makefile` - automatyzacja uruchamiania testow

**Szczegolowy opis w sekcji 7.**

---

## 3. Metryki i metering (Bramka A)

### 3.1 Zbierane metryki

System zbiera metryki na dwoch poziomach:

#### Poziom pozycji (analysis_run_item)

Kazda pozycja analizy (EAN) ma nastepujace pola metryczne w tabeli `analysis_run_items`:

| Pole | Typ | Opis |
|---|---|---|
| `latency_ms` | Integer | Czas odpowiedzi w milisekundach |
| `captcha_solves` | Integer | Liczba rozwiazanych CAPTCHA |
| `retries` | Integer | Liczba powtorzonych prob |
| `attempts` | Integer | Calkowita liczba prob |
| `network_node_id` | String(64) | Identyfikator wezla sieciowego |
| `provider_status` | String(32) | Status odpowiedzi providera |
| `scrape_status` | Enum | Status koncowy: ok, not_found, blocked, network_error, error, stopped_by_guardrail |

Pliki: `backend/app/models/analysis_run_item.py` (linie 36-42)

#### Poziom runu (agregowane)

Metryki agregowane obliczane przez `get_run_metrics()` w `backend/app/services/analysis_service.py`:

| Metryka | Opis | Formula |
|---|---|---|
| `cost_per_1000_ean` | Szacowany koszt na 1000 EAN | `(captcha_cost + network_cost) / processed * 1000` |
| `ean_per_min` | Przepustowosc (EAN na minute) | `processed / (elapsed_seconds / 60)` |
| `success_rate` | Wskaznik sukcesu | `completed / total` |
| `retry_rate` | Wskaznik powtorzonych prob | `total_retries / processed` |
| `captcha_rate` | Wskaznik CAPTCHA | `total_captcha / processed` |
| `blocked_rate` | Wskaznik zablokowanych | `blocked / total` |
| `network_error_rate` | Wskaznik bledow sieciowych | `network_error / total` |
| `avg_latency_ms` | Srednia latencja | `sum(latencies) / count(latencies)` |
| `p50_latency_ms` | Mediana latencji | Percentyl 50 |
| `p95_latency_ms` | P95 latencji | Percentyl 95 |
| `elapsed_seconds` | Czas trwania runu | `finished_at - started_at` |

### 3.2 Formula kosztu

Formula kosztu zaimplementowana w `backend/app/services/analysis_service.py` (linie 553-558):

```
gb_transfer_est = processed * 50KB / 1024 / 1024    # szacowane zuzycie transferu
captcha_cost    = (total_captcha / 1000) * COST_RATE_ACCESS_VERIFICATION
network_cost    = gb_transfer_est * COST_RATE_NETWORK_PER_GB
total_cost      = captcha_cost + network_cost
cost_per_1000   = total_cost / processed * 1000
```

Gdzie:
- `COST_RATE_NETWORK_PER_GB` = 12.53 PLN/GB (domyslnie) - koszt transferu sieciowego
- `COST_RATE_ACCESS_VERIFICATION` = 5.19 PLN/1000 - koszt weryfikacji dostepu (CAPTCHA)
- Szacowany transfer na pozycje: 50 KB

### 3.3 Eksport metryk

Metryki mozna wyeksportowac na trzy sposoby:

1. **API JSON** - `GET /api/v1/analysis/{run_id}/metrics` - pelny obiekt metryk w formacie JSON
2. **CSV** - `GET /api/v1/analysis/{run_id}/metrics/csv` - pobranie pliku CSV z metrykami
3. **Excel** - `GET /api/v1/analysis/{run_id}/metrics/excel` - pobranie pliku XLSX z metrykami (sformatowany arkusz z nazwami polskimi)

Dodatkowo metryki sa dostepne w formacie Prometheus:
- `GET /api/v1/metrics/prometheus` - metryki zagregowane kompatybilne z Prometheus/Grafana

Metryki Prometheus obejmuja:
- `jj_analysis_runs_total{status}` - liczba runow wg statusu
- `jj_active_runs` - aktywne runy
- `jj_eans_processed_total` - calkowita liczba przetworzonych EAN
- `jj_scrape_status_total{status}` - rozklad statusow
- `jj_captcha_solves_total` - suma rozwiazanych CAPTCHA
- `jj_avg_latency_ms` - srednia latencja
- `jj_ean_per_min_avg` - srednia przepustowosc
- `jj_cost_per_1000_ean_avg` - sredni koszt/1000 EAN
- `jj_stoploss_triggers_total` - suma wyzwolen stop-loss
- `jj_proxy_total`, `jj_proxy_active`, `jj_proxy_quarantined` - stan puli proxy

### 3.4 Parametry konfiguracyjne meteringu

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `COST_RATE_NETWORK_PER_GB` | 12.53 | Koszt transferu sieciowego w PLN za 1 GB |
| `COST_RATE_ACCESS_VERIFICATION` | 5.19 | Koszt weryfikacji dostepu (CAPTCHA) w PLN za 1000 operacji |
| `CAPTCHA_COST_USD` | 0.002 | Koszt jednego rozwiazania CAPTCHA w USD (uzywany w billing) |

---

## 4. Stabilnosc i bezpieczenstwo (Bramka B)

### 4.1 Mechanizm stop-loss

Plik: `backend/app/services/stoploss_service.py`

Mechanizm stop-loss automatycznie zatrzymuje analize gdy jakosc pobierania danych spada ponizej akceptowalnych progow. Dziala na zasadzie okna kroczacego (rolling window) o konfigurowalnej wielkosci (domyslnie 20 ostatnich pozycji).

#### 6 progow stop-loss

| Prog | Parametr | Domyslna wartosc | Opis |
|---|---|---|---|
| 1. Wskaznik bledow | `stoploss_max_error_rate` | 0.50 (50%) | Maksymalny udzial bledow w oknie |
| 2. Wskaznik CAPTCHA | `stoploss_max_captcha_rate` | 0.80 (80%) | Maksymalny udzial pozycji z CAPTCHA |
| 3. Kolejne bledy | `stoploss_max_consecutive_errors` | 10 | Maksymalna liczba kolejnych bledow |
| 4. Wskaznik retry | `stoploss_max_retry_rate` | 0.05 (5%) | Maksymalny udzial powtorzonych prob |
| 5. Wskaznik blokad | `stoploss_max_blocked_rate` | 0.10 (10%) | Maksymalny udzial zablokowanych pozycji |
| 6. Koszt/1000 | `stoploss_max_cost_per_1000` | 10.00 PLN | Maksymalny szacowany koszt na 1000 EAN |

#### Dzialanie

1. Kazda przetworzona pozycja jest rejestrowana w oknie kroczacym (`deque(maxlen=window_size)`)
2. Najpierw sprawdzane sa kolejne bledy (natychmiastowe wyzwolenie)
3. Progi rate-based sa sprawdzane dopiero gdy okno jest pelne
4. Przy wyzwoleniu:
   - Analiza otrzymuje status `stopped`
   - Pole `run_metadata` zawiera `stop_reason`, `stop_details`, `stopped_at_item`
   - Nieprzetworzone pozycje otrzymuja status `stopped_by_guardrail`
   - Wysylany jest alert webhook (jesli skonfigurowany)
   - Zapisywany jest wpis w logach audytu

Progi sa konfigurowane przez interfejs uzytkownika w zakladce "Ustawienia" i zapisywane w tabeli `settings`.

Integracja z workerem: `backend/app/workers/tasks.py` (linie 370-501)

### 4.2 Profil rownoleglosci 3x3

Plik: `backend/app/api/v1/analysis.py`, `docker-compose.yml`

System kontroli rownoleglosci dziala na trzech warstwach:

| Warstwa | Parametr | Domyslnie | Opis |
|---|---|---|---|
| Per-user | `CONCURRENCY_PER_USER` | 3 | Maksymalna liczba rownoczesnych analiz na uzytkownika |
| Globalny | `CONCURRENCY_GLOBAL_MAX` | 12 | Globalny limit rownoczesnych analiz |
| Worker | `CELERY_CONCURRENCY` | 3 | Rownoleglosc workera Celery |
| Run lock | Redis `nx` | 1 per run | Rozproszona blokada Redis na poziomie runu (TTL 3600s) |

Dodatkowo:
- `--prefetch-multiplier=1` - worker pobiera 1 zadanie naraz (backpressure)
- `--max-tasks-per-child=20` - worker restartuje proces po 20 zadaniach (ochrona przed wyciekami pamieci)
- `acks_late=True` - potwierdzenie zadania dopiero po zakonczeniu (odpornosc na awarie)

### 4.3 Circuit breaker

Plik: `backend/app/services/circuit_breaker.py`

Circuit breaker chroni system przed kaskadowym wywolywaniem blednego serwisu. Implementuje wzorzec z trzema stanami:

| Stan | Opis |
|---|---|
| `closed` | Normalny - zapytania przepuszczane |
| `open` | Otwarty - zapytania natychmiast odrzucane (po `failure_threshold` bledow) |
| `half_open` | Pol-otwarty - przepuszczane jedno zapytanie testowe (po `recovery_timeout`) |

Parametry:
- `failure_threshold` = 10 bledow
- `recovery_timeout` = 60 sekund

Integracja: `backend/app/workers/tasks.py` linia 58: `_scraper_breaker = CircuitBreaker(name="scraper", failure_threshold=10, recovery_timeout=60)`

### 4.4 Warstwa dostepu sieciowego

Plik: `backend/app/services/proxy_pool_service.py`

System zarzadzania pula dostepu sieciowego z:

- **Import** - plik CSV/TXT, walidacja URL (schemat http/https/socks4/socks5, port 1-65535)
- **Health scoring** - `health_score` 0.0-1.0, decay -0.05 per blad, recovery +0.02 per sukces
- **Auto-kwarantanna** - po 5 kolejnych bledach (`CONSECUTIVE_FAILS_QUARANTINE`), czas izolacji `NETWORK_QUARANTINE_TTL` (domyslnie 24h)
- **Healthcheck cykliczny** - co `NETWORK_HEALTHCHECK_INTERVAL` minut (domyslnie 5), automatyczne przywracanie po wygasnieciu kwarantanny
- **Selekcja** - proxy sortowane malejaco po `health_score`, wykluczone kwarantannowane

### 4.5 Backpressure

System implementuje backpressure na kilku poziomach:

1. **Celery prefetch** - `--prefetch-multiplier=1` - worker pobiera tylko 1 zadanie
2. **Max pending tasks** - modul pobierania danych ogranicza kolejke (`MAX_PENDING_TASKS=100`)
3. **Per-run EAN cache** - powtarzajace sie kody EAN w jednym runie sa obslugiwane z cache (bez dodatkowych zapytan)
4. **Redis run lock** - `set(f"run_lock:{run_id}", "1", nx=True, ex=3600)` - zapobieganie podwojnemu przetwarzaniu
5. **Request size limit** - middleware odrzuca zapytania > 50 MB
6. **Rate limiting** - slowapi z domyslnym limitem 200/min globalnie, 10/min na upload, 5/min na login

---

## 5. Bezpieczenstwo systemu

### 5.1 Autentykacja

#### Autentykacja UI (cookie-based)
Plik: `backend/app/main.py`

- Logowanie haslem (`UI_PASSWORD`) przez formularz HTML
- Cookie sesyjne `jj_session` z podpisem HMAC-SHA256
- Konfigurowalny czas zycia sesji (`UI_SESSION_TTL_HOURS`, domyslnie 24h)
- Flaga `httponly=True`, `samesite="strict"`, `secure` w produkcji
- Przy wylogowaniu usuwany cookie sesji i CSRF

#### Autentykacja API (JWT/HMAC)
Plik: `backend/app/services/auth_service.py`

- Tokeny HMAC-SHA256 z payloadem: `user_id:tenant_id:iat:jti:iss:aud`
- Weryfikacja issuer i audience
- Konfigurowalny TTL (`TOKEN_TTL_HOURS`, domyslnie 24h)
- Token refresh w ostatnich 25% czasu zycia
- Generowanie losowego `JWT_SECRET` gdy nie ustawiony (z ostrzezeniem), blad w produkcji

#### Autentykacja API Key
Plik: `backend/app/services/api_key_service.py`

- Klucze z prefiksem `jj_` + 32 bajty URL-safe
- Przechowywanie jako SHA-256 hash
- Rate limiting per klucz (60 req/min)
- Zakresowa kontrola dostepu (scopes: `read`, `write`, `admin`)
- Automatyczna dezaktywacja po wygasnieciu

### 5.2 Autoryzacja (multi-tenant, RBAC)

Plik: `backend/app/api/deps.py`

- Izolacja danych miedzy tenantami - kazdy run, alert, klucz API jest powiazany z `tenant_id`
- Weryfikacja dostepu do zasobow: `_verify_run_access()` sprawdza czy `run.tenant_id == current_user.tenant_id`
- Role uzytkownikow: `member`, `admin` - przechowywane w tabeli `users`
- Klucze API z zakresami: `read`, `write`, `admin`

### 5.3 Walidacja danych wejsciowych

| Typ danych | Walidacja | Plik |
|---|---|---|
| Kod EAN | Regex `^\d{8,13}$` + suma kontrolna EAN-13 | `backend/app/utils/validators.py`, `backend/app/utils/ean.py` |
| Pliki | Magic bytes (XLSX: `PK\x03\x04`, XLS: OLE2), rozmiar <= 50 MB, chunked read | `backend/app/api/v1/analysis.py` (linie 120-129) |
| URL proxy | Schemat (http/https/socks4/socks5), hostname, port 1-65535 | `backend/app/utils/validators.py` |
| Ciagi znakow | Usuwanie znakow kontrolnych, limit dlugosci (255 znakow) | `backend/app/utils/validators.py` (sanitize_string) |
| Nazwy plikow | Sanityzacja: basename, usuwanie `.`, `\x00`, `/`, `\`, limit 200 znakow, ochrona przed path traversal | `backend/app/services/import_service.py` |
| UUID kategorii | `uuid.UUID(category_id)` z obsluga `ValueError` | `backend/app/api/v1/analysis.py` |
| Lista EAN (bulk) | Max 10000 elementow, walidacja kazdego EAN | `backend/app/api/v1/analysis.py` (BulkEanRequest) |
| Kursy walut | Kod 3 litery, wartosc > 0, PLN = 1.0, jedna domyslna | `backend/app/services/settings_service.py` |

### 5.4 Rate limiting (per-endpoint)

Plik: `backend/app/core/rate_limit.py`, `deploy/nginx.conf`

| Warstwa | Limit | Opis |
|---|---|---|
| Globalny (slowapi) | 200/min | Domyslny limit na wszystkie endpointy |
| Login (slowapi) | 5/min | Limit na formularz logowania |
| Upload (slowapi) | 10/min | Limit na upload plikow |
| Import proxy (slowapi) | 5/min | Limit na import listy proxy |
| Login (nginx) | 10 req/min, burst 5 | Rate limiting na warstwie reverse proxy |
| API Key | 60/min per klucz | Limit per klucz API (in-memory) |

### 5.5 Ochrona przed atakami

| Atak | Ochrona | Lokalizacja |
|---|---|---|
| XSS | `escapeHtml()` w JavaScript, CSP header | `backend/app/templates/index.html`, `backend/app/main.py` (linia 84) |
| SQL Injection | Parameterized queries (SQLAlchemy ORM), brak raw SQL | Wszystkie serwisy |
| CSRF | Token w formularzu + cookie, weryfikacja `hmac.compare_digest` | `backend/app/main.py` (linie 239-280) |
| Brute Force (login) | 5 prob / 10 min per IP, blokada z `Retry-After` | `backend/app/main.py` (linie 176-187) |
| Brute Force (konto) | Exponential backoff: 5min, 10min, ..., max 1h | `backend/app/services/auth_service.py` (linie 49-57) |
| Path Traversal | `filepath.resolve().is_relative_to(target_dir.resolve())` | `backend/app/services/import_service.py` (linia 45) |
| File Upload | Magic bytes validation, chunked read, size limit, filename sanitization | `backend/app/api/v1/analysis.py` |
| Timing Attack | `hmac.compare_digest()` wszedzie | `backend/app/main.py`, `backend/app/services/auth_service.py` |
| DoS (OOM) | Chunked file read (1 MB), emergency dict cleanup > 10000 entries | `backend/app/main.py`, `backend/app/api/v1/analysis.py` |
| Information Leakage | Stack trace nie wyciekaja - generic error message | `backend/app/main.py` (linie 49-57), OpenAPI/Swagger wylaczone |

### 5.6 Audit logging

Plik: `backend/app/services/audit_service.py`

System logowania zdarzen bezpieczenstwa. Kazde zdarzenie zawiera:
- `timestamp` (UTC ISO 8601)
- `action` (typ zdarzenia)
- `user_id`, `tenant_id`, `ip`
- `details` (dane kontekstowe)

Logowane zdarzenia:
- `login_success`, `login_failure` - proby logowania
- `file_upload` - upload pliku analizy
- `run_cancel` - anulowanie analizy
- `settings_update` - zmiana ustawien
- `currency_rates_update` - zmiana kursow walut
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

- `security_opt: [no-new-privileges:true]` - backend i worker nie moga eskalowac uprawnien
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
- `proxy_read_timeout 300s` - timeout dla dlugich analiz

#### Hasla i sekrety
- Wymog silnego `UI_PASSWORD` w produkcji (RuntimeError jesli slabe)
- Wymog `JWT_SECRET` w produkcji
- Losowe generowanie sekretow w srodowisku dev (z ostrzezeniem)
- Hasla hashowane PBKDF2-SHA256 z unikalnym soleniem (100 000 iteracji)

---

## 6. Interfejs uzytkownika

### 6.1 Architektura UI

Interfejs jest zaimplementowany jako Single Page Application (SPA) w czystym HTML/CSS/JavaScript, bez zewnetrznych frameworkow (brak React/Vue/Angular). Caly interfejs jest zawarty w jednym pliku: `backend/app/templates/index.html`.

Cechy interfejsu:
- **Motyw ciemny/jasny** - przelacznik w stopce bocznej
- **Responsywnosc** - mobilne menu hamburger, adaptacyjne siatki
- **Brak zewnetrznych zaleznosci** - tylko font Inter z Google Fonts
- **Komunikacja z API** - `fetch()` z cookie-based autentykacja

### 6.2 Zakladki interfejsu

#### Dashboard (tab-dashboard)
Glowny panel z kartami metryk:
- Aktywne analizy (liczba)
- Status modulu pobierania danych (ikona + tekst)
- Donut chart: rozklad oplacalnosci produktow (oplacalny/nieoplacalny/nieokreslony)
- Donut chart: rozklad statusow pobierania danych (ok/not_found/error/blocked)
- Stan warstwy dostepu sieciowego (liczba aktywnych/kwarantannowanych)
- Srednia przepustowosc (EAN/min) i koszt/1000 EAN

#### Nowa analiza (tab-new-run)
- Upload pliku Excel/CSV z wyborem kategorii
- Wybor trybu: live (pobieranie na zywo) lub cached (z bazy)
- Konfiguracja filtrow: cache days, limit, zrodlo, EAN contains
- Alternatywa: start analizy z bazy danych (bez uploadu)

#### Historia (tab-history)
- Lista uruchomionych analiz z statusem, postepem, data
- Mozliwosc pobrania wynikow (Excel) i podgladu metryk

#### Panel metryk runu
Po kliknieciu w run, wyswietlane sa szczegolowe metryki:
- Koszt (cost_per_1000_ean)
- Przepustowosc (ean_per_min)
- Latencja (avg, p50, p95)
- Wskazniki: success_rate, retry_rate, captcha_rate, blocked_rate, network_error_rate
- Czas trwania
- Eksport do CSV i Excel

#### Dane rynkowe (tab-market)
- Przegladanie danych rynkowych produktow
- Filtrowanie po kategorii, statusie, EAN

#### Warstwa dostepu sieciowego (tab-proxies)
- Lista proxy z health score, liczba sukcesow/bledow
- Import z pliku (drag & drop)
- Reczna kwarantanna/przywracanie
- Podsumowanie zdrowia puli (total, active, quarantined, avg_health_score)

#### Ustawienia (tab-settings)
- Cache TTL (dni)
- Progi stop-loss (6 parametrow)
- Wlaczenie/wylaczenie stop-loss
- Rozmiar okna kroczacego
- Kursy walut (PLN, EUR, USD, CAD)

#### Pomoc (tab-help)
- Dokumentacja uzytkownika
- Opis formatow plikow
- Przyklady

#### Monitoring EAN (tab-monitoring)
- Dodawanie EAN do monitoringu cyklicznego
- Konfiguracja interwalu odswiezania (minuty)
- Priorytet monitoringu
- Lista monitorowanych EAN z datami ostatniego/nastepnego sprawdzenia

#### Alerty (tab-alerts)
- Tworzenie regul alertowych (cena ponizej/powyzej, spadek %, brak w sprzedazy)
- Historia wyzwolonych alertow
- Powiadomienia webhook

#### Klucze API (tab-api-keys)
- Tworzenie kluczy z zakresami (read, write, admin)
- Lista kluczy (prefix, zakresy, ostatnie uzycie)
- Odwolywanie kluczy

#### Konto (tab-account)
- Rejestracja i logowanie uzytkownikow
- Dane konta i sesji

#### Zuzycie (tab-usage)
- Zuzycie biezacego okresu (EAN, CAPTCHA, koszt)
- Limit quota i procent uzycia
- Historia zuzycia per miesiac

#### Administracja (tab-admin)
- Zarzadzanie tenantami (tworzenie, konfiguracja quota)

### 6.3 Raport przyczyny stop-loss

Gdy analiza jest zatrzymana przez mechanizm stop-loss, interfejs wyswietla:
- Komunikat o przyczynie zatrzymania (`stop_reason`)
- Szczegoly (np. `error_rate: 0.55, threshold: 0.50, window: 20`)
- Numer pozycji przy ktorej nastapilo zatrzymanie (`stopped_at_item`)
- Mozliwosc pobrania czesciowych wynikow

Dane sa dostepne z `run_metadata` obiektu analizy oraz przez SSE event `stopped`.

### 6.4 Eksport wynikow

- **Excel (.xlsx)** - `GET /api/v1/analysis/{run_id}/download` - pelny raport z wynikami analizy, oplacalnoscia, cenami
- **CSV** - `GET /api/v1/analysis/{run_id}/metrics/csv` - metryki runu
- **Excel metryk** - `GET /api/v1/analysis/{run_id}/metrics/excel` - sformatowany arkusz z metrykami

---

## 7. Testy

### 7.1 Podsumowanie

| Metryka | Wartosc |
|---|---|
| Calkowita liczba testow | 153 |
| Pliki testowe | 14 |
| Czas uruchomienia | < 5 sekund |
| Framework | pytest |

### 7.2 Pliki testowe i pokrycie

| Plik | Liczba testow (est.) | Zakres |
|---|---|---|
| `test_security_hardening.py` | 20+ | Hashowanie hasel, walidacja tokenow, brute force, timing attacks |
| `test_security.py` | 15+ | CSRF, sesje, cookie security, auth bypass |
| `test_stoploss.py` | 15+ | 6 progow stop-loss, okno kroczace, konfiguracja |
| `test_circuit_breaker.py` | 10+ | Stany circuit breaker, recovery, failure threshold |
| `test_validators.py` | 10+ | Walidacja EAN, proxy URL, sanityzacja |
| `test_profitability_service.py` | 10+ | Algorytm oplacalnosci, rozne scenariusze |
| `test_allegro_scraper_client.py` | 10+ | Klient HTTP, obsluga bledow, timeout |
| `test_integration.py` | 15+ | Endpointy API, upload, analiza, eksport |
| `test_excel_reader.py` | 10+ | Import Excel/CSV, konwersja walut |
| `test_excel_writer.py` | 5+ | Generowanie plikow Excel |
| `test_analysis_result_serialization.py` | 5+ | Serializacja wynikow analizy |
| `test_worker_cache_policy.py` | 10+ | Polityka cache workera |
| `test_worker_not_found_cache.py` | 5+ | Cache dla pozycji not_found |
| `test_critical_coverage.py` | 10+ | Krytyczne sciezki kodu |
| `test_no_api_remnants.py` | 3+ | Weryfikacja braku nieuzywanych API |
| `test_auth_service.py` | 10+ | Autentykacja, tokeny, refresh |

### 7.3 Kategorie testow

- **Testy jednostkowe** - izolowane testy logiki biznesowej (profitability, stoploss, circuit breaker, validators)
- **Testy integracyjne** - testy endpointow API z baza danych (upload, analiza, eksport)
- **Testy bezpieczenstwa** - CSRF, brute force, timing attacks, path traversal, XSS, password security

### 7.4 Uruchamianie testow

```bash
# Uruchomienie wszystkich testow
make test

# Rownowazne polecenie
UI_AUTH_BYPASS=1 PYTHONPATH=backend python -m pytest -q

# Uruchomienie pojedynczego pliku
UI_AUTH_BYPASS=1 PYTHONPATH=backend python -m pytest backend/app/tests/test_stoploss.py -v
```

### 7.5 Protokol testu wolumenowego

Plik: `tools/volume_test.py`

Narzedzie do automatycznego testu wolumenowego, ktore:

1. Wgrywa plik z produktami (Excel/CSV)
2. Monitoruje postep analizy w czasie rzeczywistym
3. Pobiera metryki po zakonczeniu
4. Generuje raport w formacie tekstowym z weryfikacja kryteriow odbioru

Uzycie:
```bash
# Uruchomienie testu
make volume-test

# Lub reczne
python tools/volume_test.py --url http://localhost --file sample.xlsx --output raport_test_wolumenowy.txt
```

Raport zawiera:
- Metryki kluczowe (Bramka A): koszt/1000 EAN, EAN/min
- Metryki stabilnosci (Bramka B): success rate, CAPTCHA rate, retry rate, blocked rate
- Szczegoly: liczba produktow, bledow, nie znalezionych, zablokowanych, latencja
- Kryteria odbioru: PASS/FAIL dla kazdego kryterium

---

## 8. Parametry konfiguracyjne

Plik: `backend/.env.example`

### Baza danych

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `DB_URL` | `postgresql+psycopg2://mvp:mvp@postgres:5432/mvpdb` | Connection string do PostgreSQL |

### Redis / Celery

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | URL do serwera Redis |
| `CELERY_BROKER_URL` | `${REDIS_URL}` | URL brokera Celery |
| `CELERY_RESULT_BACKEND` | `${REDIS_URL}` | URL backendu wynikow Celery |

### Bezpieczenstwo

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `JWT_SECRET` | (wymagane w produkcji) | Sekret do podpisywania tokenow JWT |
| `UI_PASSWORD` | (wymagane w produkcji) | Haslo do interfejsu uzytkownika |
| `UI_BASIC_AUTH_USER` | `admin` | Uzytkownik HTTP Basic Auth (legacy) |
| `UI_BASIC_AUTH_PASSWORD` | (wymagane) | Haslo HTTP Basic Auth (legacy) |
| `UI_SESSION_TTL_HOURS` | `24` | Czas zycia sesji w godzinach |
| `COOKIE_SECURE` | `false` | Flaga Secure na cookies (automatycznie true w produkcji) |
| `CORS_ORIGINS` | `http://localhost,http://localhost:80` | Dozwolone originy CORS |
| `REGISTRATION_KEY` | (opcjonalne) | Klucz rejestracyjny dla nowych uzytkownikow |

### Profil rownoleglosci 3x3

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `CONCURRENCY_PER_USER` | `3` | Max rownoczesnych analiz per uzytkownik |
| `CONCURRENCY_GLOBAL_MAX` | `12` | Globalny max rownoczesnych analiz |
| `MAX_CONCURRENT_RUNS` | `3` | Max rownoczesnych runow |
| `CELERY_CONCURRENCY` | `3` | Rownoleglosc workera Celery |

### Mechanizm stop-loss

Progi konfigurowane przez interfejs uzytkownika, wartosci domyslne:

| Parametr (w tabeli settings) | Domyslna wartosc | Opis |
|---|---|---|
| `stoploss_enabled` | `true` | Wlaczenie mechanizmu |
| `stoploss_window_size` | `20` | Rozmiar okna kroczacego |
| `stoploss_max_error_rate` | `0.50` | Max wskaznik bledow |
| `stoploss_max_captcha_rate` | `0.80` | Max wskaznik CAPTCHA |
| `stoploss_max_consecutive_errors` | `10` | Max kolejnych bledow |
| `stoploss_max_retry_rate` | `0.05` | Max wskaznik retry |
| `stoploss_max_blocked_rate` | `0.10` | Max wskaznik blokad |
| `stoploss_max_cost_per_1000` | `10.00` | Max koszt/1000 EAN (PLN) |

### Metering kosztu

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `COST_RATE_NETWORK_PER_GB` | `12.53` | Koszt transferu sieciowego (PLN/GB) |
| `COST_RATE_ACCESS_VERIFICATION` | `5.19` | Koszt weryfikacji dostepu (PLN/1000) |
| `CAPTCHA_COST_USD` | `0.002` | Koszt CAPTCHA (USD/szt.) |

### Warstwa dostepu sieciowego

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `NETWORK_HEALTHCHECK_INTERVAL` | `5` | Interwal healthcheckow (minuty) |
| `NETWORK_QUARANTINE_TTL` | `24` | Czas kwarantanny (godziny) |
| `SCRAPER_PROXIES_FILE` | `/workspace/data/proxies.txt` | Sciezka do pliku z lista proxy |

### Modul pobierania danych

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `ALLEGRO_SCRAPER_URL` | `http://allegro_scraper:3000` | URL modulu pobierania danych |
| `ALLEGRO_SCRAPER_POLL_INTERVAL` | `2.0` | Interwal odpytywania (sekundy) |
| `ALLEGRO_SCRAPER_TIMEOUT_SECONDS` | `90` | Timeout zapytania (sekundy) |
| `SCRAPER_WORKER_COUNT` | `3` | Liczba workerow modulu |
| `SCRAPER_CONCURRENCY_PER_WORKER` | `3` | Rownoleglosc per worker |
| `SCRAPER_MAX_TASK_RETRIES` | `2` | Max powtorzonych prob |
| `SCRAPER_MAX_PENDING_TASKS` | `100` | Max oczekujacych zadan |
| `ANYSOLVER_API_KEY` | (wymagane) | Klucz API do weryfikacji dostepu |

### Analiza oplacalnosci

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `PROFITABILITY_MIN_PROFIT_PLN` | `5.0` | Minimalny zysk absolutny (PLN) |
| `PROFITABILITY_MIN_SALES` | `10` | Minimalna liczba sprzedazy |
| `PROFITABILITY_MAX_COMPETITION` | `50` | Maksymalna liczba ofert konkurencji |
| `EUR_TO_PLN_RATE` | `4.5` | Domyslny kurs EUR/PLN |

### Srodowisko

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `ENVIRONMENT` | `dev` | Srodowisko (dev/production) |
| `WORKSPACE` | `/workspace` | Katalog roboczy |
| `LOG_FORMAT` | `text` | Format logow (text/json) |
| `LOG_LEVEL` | `INFO` | Poziom logowania |

### Alerty i powiadomienia

| Parametr | Domyslna wartosc | Opis |
|---|---|---|
| `ALERT_WEBHOOK_URL` | (opcjonalne) | URL webhooka alertow |
| `NOTIFICATION_WEBHOOK_URL` | (opcjonalne) | URL webhooka powiadomien |
| `TUNNEL_TOKEN` | (opcjonalne) | Token Cloudflare Tunnel |

---

## 9. Instrukcja uruchomienia

### 9.1 Docker Compose (pelny stack - produkcja)

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
| `allegro_scraper` | 3000 (wewn.) | Modul pobierania danych |
| `postgres` | 5432 (wewn.) | Baza danych PostgreSQL 15 |
| `redis` | 6379 (wewn.) | Broker wiadomosci i cache |
| `cloudflared` | - | Tunel Cloudflare (opcjonalnie) |

**Kolejnosc uruchomienia:**
1. `postgres` i `redis` (healthcheck)
2. `migrations` (czeka na postgres, uruchamia `alembic upgrade head`)
3. `allegro_scraper` (healthcheck)
4. `backend` i `worker` (czekaja na migracje + scraper + postgres + redis)
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

### Kryterium 10.1: Modul pozyskiwania danych pobiera dane cenowe z Allegro

**Weryfikacja:**
1. Uruchom system: `make up`
2. Zaloguj sie do interfejsu: http://localhost
3. Utworz kategorie w zakladce "Dane rynkowe"
4. Wgraj plik Excel z kodami EAN w zakladce "Nowa analiza"
5. Obserwuj postep analizy - pozycje powinny przechodzic ze statusu `pending` -> `in_progress` -> `ok`/`not_found`
6. Zweryfikuj: `GET /api/v1/analysis/{run_id}/results` - pole `allegro_price_pln` i `sold_count` wypelnione

**Dowod:** Pole `scrape_status=ok` i niepuste `allegro_price` w odpowiedzi API.

---

### Kryterium 10.2: System oblicza koszt/1000 EAN i EAN/min

**Weryfikacja:**
1. Uruchom analize (jak w 10.1)
2. Po zakonczeniu: `GET /api/v1/analysis/{run_id}/metrics`
3. Sprawdz pola `cost_per_1000_ean` i `ean_per_min` - powinny byc liczbami > 0
4. Eksportuj: `GET /api/v1/analysis/{run_id}/metrics/csv` - pobrany plik CSV z metrykami
5. Sprawdz metryki Prometheus: `GET /api/v1/metrics/prometheus` - linie `jj_cost_per_1000_ean_avg` i `jj_ean_per_min_avg`

**Dowod:** `cost_per_1000_ean` i `ean_per_min` jako wartosci liczbowe w odpowiedzi metrics.

---

### Kryterium 10.3: Mechanizm stop-loss zatrzymuje analize przy przekroczeniu progow

**Weryfikacja:**
1. W zakladce "Ustawienia", ustaw niski prog: `max_error_rate = 0.01` (1%)
2. Uruchom analize z duzym plikiem
3. Gdy przynajmniej 1 pozycja z 20 zakonczy sie bledem, analiza powinna sie zatrzymac
4. Sprawdz: `GET /api/v1/analysis/{run_id}` - `status=stopped`, `run_metadata.stop_reason=error_rate`
5. W interfejsie widoczny komunikat o przyczynie zatrzymania

**Dowod:** `status=stopped` i `run_metadata.stop_reason` w odpowiedzi API.

---

### Kryterium 10.4: Profil rownoleglosci 3x3 ogranicza liczbe rownoczesnych analiz

**Weryfikacja:**
1. Ustaw `CONCURRENCY_PER_USER=2` w `.env`
2. Uruchom 2 analizy jednoczesnie - obie powinny sie uruchomic
3. Uruchom 3. analize - powinna zostac odrzucona z HTTP 429
4. Komunikat: "Limit rownoczesnych analiz na uzytkownika (2) osiagniety."

**Dowod:** HTTP 429 z komunikatem bledu przy przekroczeniu limitu.

---

### Kryterium 10.5: Warstwa dostepu sieciowego z auto-kwarantanna

**Weryfikacja:**
1. W zakladce "Warstwa sieciowa" importuj plik z lista proxy
2. Sprawdz: `GET /api/v1/proxy-pool` - lista proxy z `health_score=1.0`
3. Sprawdz: `GET /api/v1/proxy-pool/health` - podsumowanie puli
4. Symuluj awarie: `POST /api/v1/proxy-pool/{id}/quarantine` - proxy przechodzi do kwarantanny
5. Sprawdz: `GET /api/v1/proxy-pool?include_quarantined=true` - proxy ma `quarantine_until` i `quarantine_reason`
6. Przywroc: `DELETE /api/v1/proxy-pool/{id}/quarantine`

**Dowod:** Pola `quarantine_until` i `health_score` w odpowiedzi API.

---

### Kryterium 10.6: Circuit breaker chroni przed kaskadowymi bledami

**Weryfikacja:**
1. Sprawdz w logach workera: `CIRCUIT_BREAKER scraper OPEN after 10 failures` po 10 kolejnych bledach modulu pobierania danych
2. Kolejne pozycje otrzymuja status `error` z komunikatem `circuit_breaker_open` (bez obciazania modulu)
3. Po `recovery_timeout` (60s): `CIRCUIT_BREAKER scraper half-open (trying recovery)`
4. Jesli nastepne zapytanie jest sukcesem: `CIRCUIT_BREAKER scraper recovered`

**Dowod:** Logi workera oraz `error_message=circuit_breaker_open` w pozycjach analizy.

---

### Kryterium 10.7: System zapewnia bezpieczenstwo autentykacji

**Weryfikacja:**
1. Wejdz na http://localhost - przekierowanie na /login
2. Podaj bledne haslo 5 razy - blokada 10 minut (HTTP 429)
3. Zaloguj sie poprawnie - cookie `jj_session` z `httponly`, `samesite=strict`
4. Wejdz na API bez cookie: `curl http://localhost/api/v1/analysis` - HTTP 401
5. Sprawdz audit log: `AUDIT: login_failure` i `AUDIT: login_success`

**Dowod:** HTTP 429 po 5 probach, HTTP 401 bez autentykacji, wpisy w logach audytu.

---

### Kryterium 10.8: Security headers sa aktywne

**Weryfikacja:**
```bash
curl -I http://localhost/
```
Oczekiwane naglowki:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'self'; ...`
- Brak naglowka `Server: nginx/x.x.x` (ukryty)

**Dowod:** Naglowki w odpowiedzi HTTP.

---

### Kryterium 10.9: Eksport wynikow do Excel i CSV

**Weryfikacja:**
1. Uruchom analize i poczekaj na zakonczenie
2. Pobierz wyniki: `GET /api/v1/analysis/{run_id}/download` - plik `.xlsx`
3. Pobierz metryki CSV: `GET /api/v1/analysis/{run_id}/metrics/csv` - plik `.csv`
4. Pobierz metryki Excel: `GET /api/v1/analysis/{run_id}/metrics/excel` - plik `.xlsx`
5. Otworz pliki - zweryfikuj zawartosc (EAN, ceny, oplacalnosc, metryki)

**Dowod:** Poprawne pliki Excel/CSV z danymi analizy.

---

### Kryterium 10.10: 153 testy automatyczne przechodza

**Weryfikacja:**
```bash
make test
```

Oczekiwany wynik:
```
153 passed in X.XXs
```

**Dowod:** Wynik uruchomienia `make test` z 153 testami.

---

### Kryterium 10.11: Protokol testu wolumenowego dziala

**Weryfikacja:**
```bash
make volume-test
```

Lub recznie:
```bash
python tools/volume_test.py --url http://localhost --file sample.xlsx --output raport.txt
```

Oczekiwany wynik: raport z sekcjami "METRYKI KLUCZOWE", "METRYKI STABILNOSCI", "KRYTERIA ODBIORU" i weryfikacja PASS/FAIL.

**Dowod:** Wygenerowany raport z wynikami testu wolumenowego.

---

### Kryterium 10.12: System dziala w kontenerach Docker

**Weryfikacja:**
```bash
docker compose up --build
docker compose ps
```

Wszystkie 7 serwisow powinny miec status `healthy` lub `running`:
- `postgres` (healthy)
- `redis` (healthy)
- `allegro_scraper` (healthy)
- `backend` (healthy)
- `worker` (healthy)
- `nginx` (running)
- `cloudflared` (running)

**Dowod:** `docker compose ps` z wszystkimi serwisami w stanie zdrowym.

---

## Podsumowanie

Etap 1 projektu Jolly Jesters zostal zrealizowany w pelnym zakresie, obejmujac:

- **Modul pozyskiwania danych** z cache, retry, backpressure i provider abstraction
- **System metryki kosztowej** z formula kosztu i eksportem do CSV/Excel/Prometheus
- **6 mechanizmow stabilnosci** - stop-loss, 3x3, circuit breaker, proxy pool, backpressure, healthcheck
- **Kompleksowe bezpieczenstwo** - JWT, CSRF, rate limiting, audit logging, security headers, Docker hardening
- **Interfejs uzytkownika** z 13 zakladkami, ciemnym/jasnym motywem i responsywnoscia
- **Warstwa SaaS** - multi-tenant, billing, API keys z zakresami, monitoring cykliczny, alerty
- **153 testy automatyczne** pokrywajace logike biznesowa, integracje i bezpieczenstwo
- **Infrastruktura produkcyjna** - Docker Compose z 7 serwisami, nginx, migracje, CI/CD

System jest gotowy do produkcyjnego wdrozenia i spelnia wszystkie kryteria odbioru zdefiniowane w specyfikacji Etapu 1.
