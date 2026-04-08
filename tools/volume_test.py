#!/usr/bin/env python3
"""
Protokol testu wolumenowego - Etap 1
Uruchamia test z zadana liczba EAN i generuje raport odbioru.

Uzycie:
    python tools/volume_test.py --url http://localhost --file sample.xlsx
    python tools/volume_test.py --url http://localhost --file sample.xlsx --output raport.txt
"""
import argparse
import requests
import time
import json
import sys
from datetime import datetime


def run_volume_test(base_url, file_path, output_path=None):
    print(f"=== PROTOKOL TESTU WOLUMENOWEGO ===")
    print(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"URL: {base_url}")
    print(f"Plik: {file_path}")
    print()

    # 1. Upload file
    print("[1/4] Upload pliku...")
    with open(file_path, "rb") as f:
        resp = requests.post(f"{base_url}/api/v1/analysis/upload", files={"file": f})
    if resp.status_code != 200:
        print(f"BLAD: Upload nie powiodl sie: {resp.status_code} {resp.text}")
        sys.exit(1)

    data = resp.json()
    run_id = data["analysis_run_id"]
    print(f"   Run ID: {run_id}")

    # 2. Monitor progress
    print("[2/4] Monitorowanie postepu...")
    start_time = time.time()
    while True:
        resp = requests.get(f"{base_url}/api/v1/analysis/{run_id}")
        if resp.status_code != 200:
            print(f"   BLAD: Nie mozna pobrac statusu: {resp.status_code}")
            time.sleep(5)
            continue

        status_data = resp.json()
        status = status_data.get("status", "unknown")
        total = status_data.get("total_products", 0)
        processed = status_data.get("processed_products", 0)
        pct = round(processed / total * 100, 1) if total > 0 else 0

        print(f"   Status: {status} | Postep: {processed}/{total} ({pct}%)")

        if status in ("completed", "failed", "canceled", "stopped"):
            break

        time.sleep(5)

    elapsed = round(time.time() - start_time, 1)

    # 3. Collect metrics
    print("[3/4] Pobieranie metryk...")
    resp = requests.get(f"{base_url}/api/v1/analysis/{run_id}/metrics")
    metrics = resp.json() if resp.status_code == 200 else {}

    # 4. Generate report
    print("[4/4] Generowanie raportu...")
    print()

    report_lines = [
        "=" * 60,
        "RAPORT Z TESTU WOLUMENOWEGO - ETAP 1",
        "=" * 60,
        f"Data testu:           {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Srodowisko:           {base_url}",
        f"Plik wejsciowy:       {file_path}",
        f"Run ID:               {run_id}",
        f"Status koncowy:       {status}",
        f"Czas trwania:         {elapsed}s",
        "",
        "--- METRYKI KLUCZOWE (Bramka A) ---",
        f"Koszt/1000 EAN (est.): {metrics.get('cost_per_1000_ean', 'brak danych')}",
        f"EAN/min:               {metrics.get('ean_per_min', 'brak danych')}",
        "",
        "--- METRYKI STABILNOSCI (Bramka B) ---",
        f"Success rate:          {metrics.get('success_rate', 'brak danych')}",
        f"CAPTCHA rate:          {metrics.get('captcha_rate', 'brak danych')}",
        f"Retry rate:            {metrics.get('retry_rate', 'brak danych')}",
        f"Blocked rate:          {metrics.get('blocked_rate', 'brak danych')}",
        f"Network error rate:    {metrics.get('network_error_rate', 'brak danych')}",
        "",
        "--- SZCZEGOLY ---",
        f"Produkty ogolnie:      {metrics.get('total_items', 'brak danych')}",
        f"Zakonczone:            {metrics.get('completed_items', 'brak danych')}",
        f"Bledy:                 {metrics.get('failed_items', 'brak danych')}",
        f"Nie znalezione:        {metrics.get('not_found_items', 'brak danych')}",
        f"Zablokowane:           {metrics.get('blocked_items', 'brak danych')}",
        f"Srednia latencja:      {metrics.get('avg_latency_ms', 'brak danych')} ms",
        f"P95 latencja:          {metrics.get('p95_latency_ms', 'brak danych')} ms",
        "",
        "--- KRYTERIA ODBIORU ---",
    ]

    # Acceptance criteria checks
    ean_per_min = metrics.get("ean_per_min")
    cost = metrics.get("cost_per_1000_ean")
    captcha_rate = metrics.get("captcha_rate")
    retry_rate = metrics.get("retry_rate")
    success_rate = metrics.get("success_rate")

    checks = []
    if ean_per_min is not None:
        checks.append(f"[{'PASS' if ean_per_min > 0 else 'FAIL'}] Przepustowosc > 0 EAN/min: {ean_per_min}")
    if cost is not None:
        checks.append(f"[{'PASS' if cost < 15 else 'WARN'}] Koszt/1000 < 15 PLN: {cost}")
    if success_rate is not None:
        checks.append(f"[{'PASS' if success_rate > 0.5 else 'FAIL'}] Success rate > 50%: {success_rate}")
    if status == "completed":
        checks.append("[PASS] Run zakonczony poprawnie")
    elif status == "stopped":
        checks.append("[INFO] Run zatrzymany przez mechanizm stop-loss")
        meta = status_data.get("run_metadata", {})
        if meta.get("stop_reason"):
            checks.append(f"       Przyczyna: {meta['stop_reason']}")
    else:
        checks.append(f"[FAIL] Run zakonczony ze statusem: {status}")

    report_lines.extend(checks)
    report_lines.extend(["", "=" * 60])

    report = "\n".join(report_lines)
    print(report)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nRaport zapisany do: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test wolumenowy - Etap 1")
    parser.add_argument("--url", default="http://localhost", help="Base URL systemu")
    parser.add_argument("--file", required=True, help="Plik wejsciowy (xlsx/csv)")
    parser.add_argument("--output", help="Sciezka do pliku raportu")
    args = parser.parse_args()

    run_volume_test(args.url, args.file, args.output)
