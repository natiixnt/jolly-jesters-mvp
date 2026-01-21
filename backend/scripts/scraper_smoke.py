import argparse
import asyncio
import csv
import json
import random
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.local_scraper_client import check_local_scraper_health, fetch_via_local_scraper  # noqa: E402


def _load_eans(path: Path | None, limit: int | None) -> List[str]:
    eans: List[str] = []
    if path and path.exists():
        for line in path.read_text().splitlines():
            digits = "".join(ch for ch in line if ch.isdigit())
            if digits:
                eans.append(digits)
    if not eans:
        eans = [
            "5901234123457",
            "9788379246326",
            "8717163999581",
            "4005900130631",
            "5903571254410",
        ]
    if limit:
        eans = eans[:limit]
    return eans


def _outcome_label(result) -> str:
    if getattr(result, "blocked", False):
        return "blocked"
    if getattr(result, "is_not_found", False):
        return "not_found"
    if getattr(result, "is_temporary_error", False):
        return "error"
    return "success"


async def _scrape_one(ean: str, delay: float, jitter: float) -> Dict[str, object]:
    start = time.monotonic()
    result = await fetch_via_local_scraper(ean)
    duration = time.monotonic() - start
    payload = getattr(result, "raw_payload", {}) or {}
    attempt = payload.get("attempt") or payload.get("raw", {}).get("attempt")
    retry_after = payload.get("retry_after_seconds")
    return {
        "ean": ean,
        "outcome": _outcome_label(result),
        "blocked": bool(getattr(result, "blocked", False)),
        "not_found": bool(getattr(result, "is_not_found", False)),
        "temporary_error": bool(getattr(result, "is_temporary_error", False)),
        "price": getattr(result, "price", None),
        "sold_count": getattr(result, "sold_count", None),
        "fingerprint_id": payload.get("fingerprint_id"),
        "proxy_id": payload.get("proxy_id"),
        "block_reason": payload.get("block_reason"),
        "request_status_code": payload.get("request_status_code"),
        "duration_seconds": round(duration, 2),
        "attempt": attempt,
        "retry_after_seconds": retry_after,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke/batch runner for local Allegro scraper.")
    parser.add_argument("--ean-file", type=Path, help="Path to file with EANs (one per line).")
    parser.add_argument("--limit", type=int, default=20, help="Limit number of EANs to run.")
    parser.add_argument("--delay", type=float, default=1.5, help="Base delay between requests (seconds).")
    parser.add_argument("--jitter", type=float, default=0.7, help="Random jitter added to delay (seconds).")
    parser.add_argument("--output", type=Path, help="Optional CSV file to store detailed results.")
    args = parser.parse_args()

    health = check_local_scraper_health(timeout_seconds=2.0)
    print(f"[health] {health}")
    eans = _load_eans(args.ean_file, args.limit)
    print(f"[plan] running {len(eans)} EANs with delay={args.delay}s jitter={args.jitter}s")

    results: List[Dict[str, object]] = []
    for idx, ean in enumerate(eans, start=1):
        outcome = await _scrape_one(ean, args.delay, args.jitter)
        results.append(outcome)
        print(
            f"[{idx}/{len(eans)}] ean={ean} outcome={outcome['outcome']} "
            f"blocked={outcome['blocked']} status={outcome.get('request_status_code')} "
            f"block_reason={outcome.get('block_reason')} duration={outcome['duration_seconds']}s"
        )
        retry_after = outcome.get("retry_after_seconds")
        if outcome.get("block_reason") == "cooldown" and retry_after:
            sleep_for = float(retry_after) + random.uniform(0, args.jitter)
            print(f"[pause] cooldown sleeping {sleep_for:.2f}s")
            await asyncio.sleep(max(0.0, sleep_for))
        else:
            pause = args.delay + random.uniform(0, args.jitter)
            await asyncio.sleep(max(0.0, pause))

    summary: Dict[str, int] = {"success": 0, "not_found": 0, "blocked": 0, "error": 0}
    for item in results:
        summary[item["outcome"]] = summary.get(item["outcome"], 0) + 1

    print("[summary]", json.dumps(summary, ensure_ascii=False))
    if args.output:
        fieldnames = sorted({key for row in results for key in row.keys()})
        with args.output.open("w", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"[summary] saved CSV to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
