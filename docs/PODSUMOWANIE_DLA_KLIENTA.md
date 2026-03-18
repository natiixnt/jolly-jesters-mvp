# Podsumowanie prac - Jolly Jesters MVP
## Modernizacja architektury: kontrola kosztów, stabilność, skalowanie

Data: 2026-03-18

---

## 1. Kontekst i cel

System Jolly Jesters MVP umożliwia automatyczną analizę opłacalności produktów na Allegro.pl na podstawie kodów EAN. Dotychczasowa wersja działała funkcjonalnie, ale miała istotne ograniczenia:

- **Brak widoczności kosztowej** - nie było wiadomo, ile kosztuje przetworzenie 1000 EAN-ów
- **Brak mechanizmów ochronnych** - system przetwarzał dane niezależnie od liczby błędów i kosztów
- **Brak kontroli skalowania** - jeden run na raz, brak limitów, brak zarządzania proxy
- **Brak standaryzacji** - ścisłe powiązanie z jednym dostawcą danych

Celem prac było wprowadzenie kontroli kosztowej, mechanizmów bezpieczeństwa i przygotowanie systemu pod skalowanie - bez przebudowy całej architektury.

---

## 2. Co zostało zrobione

### A. Metering i kontrola kosztów

Wprowadzono granularne metryki na poziomie każdego produktu (EAN) i każdego uruchomienia analizy:

- **Czas odpowiedzi** (latency) per EAN - ile trwa pobranie danych
- **Liczba prób** (retries, proxy attempts) - ile razy system musiał ponawiać zapytanie
- **Liczba rozwiązanych CAPTCHA** - bezpośredni driver kosztu
- **Wskaźniki jakościowe per run:**
  - koszt estymowany na 1000 EAN
  - przepustowość (EAN/min)
  - wskaźnik błędów, blokad, CAPTCHA
  - percentyle czasu odpowiedzi (P50, P95)

Metryki dostępne są przez dedykowany endpoint API (`GET /analysis/{id}/metrics`), co daje podstawę pod przyszły dashboard i decyzje cenowe.

### B. Mechanizm stop-loss (automatyczna ochrona)

System automatycznie zatrzymuje analizę, gdy wykryje pogorszenie jakości:

- **Próg błędów** - jeśli >50% ostatnich 20 EAN-ów kończy się błędem
- **Próg CAPTCHA** - jeśli >80% wyników wymaga rozwiązania CAPTCHA (sygnał wysokiego kosztu)
- **Błędy z rzędu** - 10 kolejnych błędów = natychmiastowe zatrzymanie

Wszystkie progi są konfigurowalne przez API ustawień. Po zatrzymaniu run otrzymuje status `stopped` z pełnym raportem przyczyny (widocznym w API i strumieniu SSE).

### C. Zarządzanie warstwą sieciową (Network Pool)

Wprowadzono system zarządzania proxy z automatycznym scoringiem jakości:

- **Import proxy** z pliku CSV/TXT przez API
- **Health scoring** (0.0-1.0) - każde proxy jest oceniane na podstawie historii sukcesów i błędów
- **Auto-kwarantanna** - proxy z 5 kolejnymi błędami jest automatycznie wycofywane na 15 minut
- **Dashboard zdrowia** - endpoint pokazujący ile proxy jest aktywnych, w kwarantannie, jaki jest średni health score
- **Manualna kwarantanna** - możliwość ręcznego wycofania problematycznego proxy

Scraper raportuje z którego proxy korzystał i czy operacja się powiodła, co zasila system scoringu.

### D. Kontrolowane skalowanie (profil 3x3)

Przejście z sekwencyjnego przetwarzania (1 task na raz) do kontrolowanej równoległości:

- **Scraper:** 3 workery x 3 równoczesne zadania = do 9 scrapów jednocześnie
- **Celery:** 3 równoczesne analizy (zamiast 1)
- **Limit per użytkownik:** max 3 aktywne analizy jednocześnie (konfigurowalny)
- **Backpressure:** gdy kolejka scrapera jest pełna (>100 zadań), zwraca 429 - backend automatycznie czeka i ponawia z rosnącym opóźnieniem
- **Fair-share queuing:** zadania z różnych analiz są obsługiwane sprawiedliwie, bez zagładzania mniejszych runów przez duże

### E. Standaryzacja dostawców danych (Provider Abstraction)

Wprowadzono abstrakcyjny interfejs dostawcy danych:

- **Interfejs:** `fetch(ean)` + `health()` - jednolity dla każdego marketplace
- **Rejestr dostawców** - wybór aktywnego dostawcy przez zmienną środowiskową `PROVIDER_MODE`
- **Gotowość na nowe marketplace** - dodanie nowego dostawcy = jeden plik implementujący interfejs, bez zmian w logice analiz

Obecny scraper Allegro.pl został opakowany jako pierwszy provider.

---

## 3. Co to daje biznesowo

| Przed | Po |
|-------|-----|
| Brak wiedzy o koszcie przetwarzania | Estymacja kosztu per 1000 EAN w czasie rzeczywistym |
| Run idzie do końca niezależnie od błędów | Automatyczne zatrzymanie przy pogorszeniu jakości |
| 1 analiza na raz, brak kontroli | Do 3 analiz równolegle z limitami i backpressure |
| Proxy na ślepo (round-robin) | Scoring jakości, auto-kwarantanna słabych IP |
| Ścisłe powiązanie z Allegro | Abstrakcja providerów - gotowość na nowe marketplace |
| Brak danych do wyceny usługi | Metryki per run = podstawa pod model cenowy |

---

## 4. Zakres techniczny

### Nowe pliki (10)
- 3 migracje bazy danych (Alembic)
- `StopLossChecker` - serwis ochronny
- `NetworkProxy` model + serwis proxy pool
- API proxy pool (import, health, kwarantanna)
- Provider: interfejs bazowy, implementacja Allegro, rejestr

### Zmodyfikowane pliki (15+)
- Model danych analiz - 4 nowe kolumny metryczne
- Scraper (Node.js) - raportowanie retries, proxy hash, backpressure
- Backend - przechwytywanie metryk, scoring proxy, stop-loss w pętli przetwarzania
- API - endpoint metryk, limity równoczesności, obsługa statusu `stopped`
- Docker Compose - profil 3x3, nowe zmienne konfiguracyjne
- Ustawienia - konfiguracja stop-loss przez API

### Migracje bazy danych
1. Kolumny metryczne w tabeli wyników (`latency_ms`, `captcha_solves`, `retries`, `attempts`)
2. Konfiguracja stop-loss w ustawieniach + nowy status `stopped`
3. Nowa tabela `network_proxies` (scoring, kwarantanna)

---

## 5. Co zostaje na kolejne etapy

### Etap 2: Observability + UI (rekomendowany jako następny)
- Panel metryk w interfejsie użytkownika (live dashboard)
- Eksport metryk do pliku
- Alerty (webhook/email przy stop-loss)

### Etap 3: SaaS readiness (długoterminowy)
- Multi-tenant (wielu klientów, izolacja danych)
- Billing i limity per klient
- CI/CD pipeline (staging/produkcja)
- Monitoring (Prometheus/Grafana) i alerting

---

## 6. Jak wdrożyć

Wdrożenie wymaga:

1. **Migracja bazy:** `alembic upgrade head` (3 nowe migracje, automatycznie przy starcie docker-compose)
2. **Rebuild kontenerów:** `docker-compose build` (zmiany w backendzie i scraperze)
3. **Opcjonalne zmienne środowiskowe:**

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `SCRAPER_WORKER_COUNT` | 3 | Liczba workerów scrapera |
| `SCRAPER_CONCURRENCY_PER_WORKER` | 3 | Równoczesność per worker |
| `SCRAPER_MAX_PENDING_TASKS` | 100 | Limit kolejki (backpressure) |
| `CELERY_CONCURRENCY` | 3 | Równoczesne analizy Celery |
| `MAX_CONCURRENT_RUNS` | 3 | Limit aktywnych analiz |
| `CAPTCHA_COST_USD` | 0.002 | Koszt per CAPTCHA (do estymacji) |
| `PROVIDER_MODE` | allegro_scraper | Aktywny dostawca danych |

Wszystkie zmiany są wstecznie kompatybilne - istniejące dane nie wymagają modyfikacji.
