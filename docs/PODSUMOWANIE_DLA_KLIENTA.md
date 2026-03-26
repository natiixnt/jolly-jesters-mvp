# Jolly Jesters - platforma analizy oplacalnosci produktow
## Podsumowanie systemu

Data: 2026-03-26

---

## 1. Co to jest

Jolly Jesters to platforma SaaS do automatycznej analizy oplacalnosci produktow na Allegro.pl. System pobiera ceny z Allegro na podstawie kodow EAN, porownuje je z ceną zakupu i okresla, ktore produkty sa oplacalne do sprzedazy.

Platforma obsluguje dwa modele:
- **B2C** - pojedynczy uzytkownik, prosty panel, ograniczone limity
- **B2B** - organizacja z wieloma uzytkownikami, API, wyzsze limity, integracje

---

## 2. Architektura systemu

### Warstwa prezentacji (UI)
- Panel administracyjny z dashboardem systemowym
- 10 zakladek: Dashboard, Proxy pool, Ustawienia, Nowa analiza, Historia, Market data, Monitoring, Alerty, Klucze API, Pomoc
- Ciemny/jasny motyw, responsywny sidebar, fuzzy search w dokumentacji
- Onboarding dla nowych uzytkownikow (4 kroki, mozliwosc zamkniecia na stale)
- Logowanie: haslo panelu (single-tenant) lub email + haslo (multi-tenant)

### Warstwa API (Backend - FastAPI)
- 16 routerow API (`/api/v1/...`)
- Autoryzacja: cookie-session (UI) + HMAC token (API) + API key (B2B)
- Ochrona brute-force: max 8 prob logowania / 10 min / IP
- CORS, secure cookies, rate limiting

### Warstwa danych (PostgreSQL)
- 18 tabel (modele SQLAlchemy + Alembic migracje)
- Produkty, dane rynkowe, historia cen, analizy, metryki
- Multi-tenant: izolacja danych per organizacja
- Cache wynikow scrapowania (konfigurowalne TTL)

### Warstwa scrapowania (Node.js + Celery)
- Scraper Allegro.pl z rotacja proxy i obsluga CAPTCHA
- 3 workery x 3 rownolegle zadania = 9 jednoczesnych scrapow
- Kolejka Redis z fair-share queuing i backpressure
- Circuit breaker (10 bledow = 60s przerwy)

### Warstwa analityczna
- Ocena oplacalnosci (mnoznik, marza, prowizja, sprzedaz)
- Metryki per EAN i per run (latency, retries, CAPTCHA, cost)
- Alerty cenowe (reguly uzytkownika)
- Eksport do Excel z metrykami

---

## 3. Glowne funkcje

### A. Analiza oplacalnosci (core)
- Upload pliku Excel/CSV z EAN-ami i cenami zakupu
- Analiza z bazy (cache) lub live scraping
- Bulk API - lista EAN-ow przez JSON
- Wynik: cena Allegro, marza, oplacalnosc, liczba sprzedanych
- Eksport do Excel z pelnym zestawem danych

### B. Monitoring EAN (ciagly)
- Watchlist: dodawanie EAN-ow do stalego monitorowania
- Konfigurowalne interwaly odswiezania (15 min - 24h)
- Bulk import (wklejenie listy lub CSV)
- Celery Beat co 60s sprawdza ktore EAN-y wymagaja odswiezenia
- Priorytetyzacja: wazniejsze EAN-y sprawdzane czesciej

### C. Alerty cenowe
- Reguly: cena spadla ponizej X, wzrosla powyzej X, spadek o X%, produkt niedostepny
- Per EAN lub globalne (wszystkie monitorowane)
- Automatyczna ewaluacja po kazdym scrapie
- Historia zdarzen z detalami (cena przed/po, prog)
- Powiadomienia in-app + webhook

### D. Metering i kontrola kosztow
- Koszt estymowany per 1000 EAN
- Przepustowosc (EAN/min)
- Retry rate, CAPTCHA rate, blocked rate
- Latency: srednia, P50, P95
- Metryki Prometheus-compatible (`/api/v1/metrics/prometheus`)

### E. Stop-loss (ochrona automatyczna)
- Rolling window: ostatnie 20 EAN-ow
- Progi: error rate >50%, CAPTCHA rate >80%, 10 bledow z rzedu
- Automatyczne zatrzymanie z raportem przyczyny
- Webhook alert przy stop-loss
- Konfigurowalne przez API ustawien

### F. Zarzadzanie proxy
- Import z pliku TXT lub wklejenie listy
- Health scoring (0.0-1.0) na podstawie historii
- Auto-kwarantanna slabych proxy (15 min)
- Dashboard zdrowia: aktywne, w kwarantannie, success rate
- Wykresy donut: rozklad statusow scrapowania i zdrowia proxy

### G. Multi-tenant i billing
- Rejestracja organizacji (tenant) z planem (free/pro/enterprise)
- Uzytkownicy z rolami (owner, admin, member)
- Quota EAN-ow per miesiac
- Tracking zuzycia: EAN-y, CAPTCHA, koszt per okres
- Limity rownoleglych analiz per tenant

### H. Klucze API (B2B)
- Generowanie kluczy `jj_xxx...` z SHA256 hash
- Prefix widoczny, pelny klucz tylko przy tworzeniu
- Revoke w dowolnym momencie
- Autentykacja: `Authorization: Bearer jj_xxx...`
- Endpointy: historia cen, market data, monitoring, analizy, alerty

### I. Abstrakcja dostawcow
- Interfejs: `fetch(ean)` + `health()`
- Rejestr dostawcow (PROVIDER_MODE env)
- Obecny: Allegro.pl scraper
- Dodanie nowego marketplace = 1 plik, bez zmian w logice

---

## 4. Endpointy API

| Grupa | Prefix | Opis |
|-------|--------|------|
| Analizy | `/api/v1/analysis/` | Upload, start, status, wyniki, metryki, porownanie, SSE stream |
| Kategorie | `/api/v1/categories/` | CRUD kategorii z mnoznikiem i prowizja |
| Market data | `/api/v1/market-data` | Przegladanie danych rynkowych z filtrami |
| Historia cen | `/api/v1/price-history/{ean}` | Snapshoty cen per EAN |
| Monitoring | `/api/v1/monitoring/` | Watch/unwatch EAN, bulk, lista |
| Alerty | `/api/v1/alerts/` | Reguly CRUD, zdarzenia |
| Powiadomienia | `/api/v1/notifications/` | Lista, mark read, unread count |
| Klucze API | `/api/v1/api-keys/` | Tworzenie, lista, revoke |
| Proxy pool | `/api/v1/proxy-pool/` | Import, health, kwarantanna |
| Ustawienia | `/api/v1/settings/` | Stop-loss, cache TTL, waluty |
| Billing | `/api/v1/billing/` | Zuzycie, quota, historia |
| Tenants | `/api/v1/tenants/` | Rejestracja, logowanie |
| Metryki | `/api/v1/metrics/prometheus` | Format Prometheus (8 metryk) |
| Status | `/api/v1/status` | Healthcheck: DB, scraper, Redis |

---

## 5. Model danych (18 tabel)

| Tabela | Opis |
|--------|------|
| `categories` | Kategorie produktow z mnoznikiem i prowizja |
| `products` | Produkty (EAN + cena zakupu) w kategorii |
| `product_market_data` | Snapshoty cen z Allegro (historia) |
| `product_effective_state` | Zdekormalizowany aktualny stan produktu |
| `analysis_runs` | Uruchomienia analiz (status, metadane) |
| `analysis_run_items` | Wyniki per EAN w analizie (cena, oplacalnosc, metryki) |
| `analysis_run_tasks` | Powiazanie z taskami Celery |
| `settings` | Konfiguracja globalna (cache, stop-loss) |
| `currency_rates` | Kursy walut (EUR/PLN itp.) |
| `network_proxies` | Pool proxy ze scoringiem i kwarantanna |
| `tenants` | Organizacje (plan, limity, SLA) |
| `users` | Uzytkownicy z rolami |
| `usage_records` | Zuzycie per tenant per miesiac |
| `monitored_eans` | Watchlist EAN-ow z interwałami |
| `alert_rules` | Reguly alertow cenowych |
| `alert_events` | Historia wystrzelonych alertow |
| `notifications` | Powiadomienia (in-app, email, webhook) |
| `api_keys` | Klucze B2B (hash SHA256) |

---

## 6. Infrastruktura (Docker Compose)

| Serwis | Technologia | Opis |
|--------|------------|------|
| `postgres` | PostgreSQL 15 | Baza danych |
| `redis` | Redis 7 | Broker kolejki + cache |
| `migrations` | Alembic | Automatyczne migracje przy starcie |
| `allegro_scraper` | Node.js | Scraper Allegro z proxy i CAPTCHA |
| `backend` | FastAPI + Uvicorn | API + UI |
| `worker` | Celery | Przetwarzanie analiz + monitoring |
| `nginx` | Nginx 1.27 | Reverse proxy |
| `cloudflared` | Cloudflare Tunnel | Tunel do stabilnego hostname |

Profile:
- `docker-compose.yml` - standardowy (3x3 scaling)
- `docker-compose.dev.yml` - development (hot reload, auth bypass)
- `docker-compose.prod.yml` - produkcja (required secrets, resource limits)

---

## 7. Zmienne konfiguracyjne

| Zmienna | Domyslna | Opis |
|---------|----------|------|
| `UI_PASSWORD` | 1234 | Haslo panelu (single-tenant) |
| `SCRAPER_WORKER_COUNT` | 3 | Liczba workerow scrapera |
| `SCRAPER_CONCURRENCY_PER_WORKER` | 3 | Rownoleglosc per worker |
| `SCRAPER_MAX_PENDING_TASKS` | 100 | Limit kolejki (backpressure) |
| `CELERY_CONCURRENCY` | 3 | Rownoczesne analizy Celery |
| `MAX_CONCURRENT_RUNS` | 3 | Limit aktywnych analiz |
| `CAPTCHA_COST_USD` | 0.002 | Koszt per CAPTCHA |
| `PROVIDER_MODE` | allegro_scraper | Aktywny dostawca danych |
| `ALERT_WEBHOOK_URL` | - | URL webhooka dla alertow |
| `NOTIFICATION_WEBHOOK_URL` | - | URL webhooka dla powiadomien |

---

## 8. Bezpieczenstwo

- Haslo sesji: HMAC-SHA256 signed cookie z TTL
- Brute-force protection: 8 prob / 10 min / IP
- API keys: SHA256 hash, prefix-only display
- CORS: konfigurowalny origin
- Circuit breaker: 10 bledow = auto-przerwa 60s
- Stop-loss: automatyczne zatrzymanie przy degradacji
- Distributed lock: Redis-based, zapobiega duplikatom
- Input validation: limity uploadu, paginacji, sanityzacja ILIKE

---

## 9. Jak wdrozyc

```bash
# 1. Klonowanie
git clone https://github.com/natiixnt/jolly-jesters-mvp.git
cd jolly-jesters-mvp

# 2. Konfiguracja
cp backend/.env.example backend/.env
# Edytuj .env - ustaw UI_PASSWORD, klucze itp.

# 3. Start
docker-compose up -d

# Migracje bazy uruchomia sie automatycznie.
# Panel dostepny na http://localhost:80
```

---

## 10. Co dalej (roadmapa)

| Priorytet | Funkcja | Opis |
|-----------|---------|------|
| Wysoki | Scheduled re-analysis | Cron - automatyczne odswiezanie cen co X dni |
| Wysoki | Email notifications | Wysylka alertow na email |
| Sredni | Comparison view | Porownanie dwoch runow - co sie zmienilo |
| Sredni | Google Sheets export | Eksport wynikow do arkusza |
| Sredni | Load testing | Skrypt k6/locust do testow wydajnosci |
| Niski | Database backup script | Automatyczny pg_dump |
| Niski | Grafana dashboard | Template z metryk Prometheus |
| Niski | Mobile responsive | Pelna obsluga mobile |
