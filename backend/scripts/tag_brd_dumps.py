#!/usr/bin/env python
"""
Quick-and-dirty classifier for BRD dump HTML files.
Tags known anti-bot providers (Cloudflare/Turnstile, DataDome, PerimeterX, reCAPTCHA, hCaptcha, Akamai),
429/rate-limit and generic access-denied pages. Intended for on-host debugging; no network access.
"""

from __future__ import annotations

import glob
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import List

DUMP_DIR = os.environ.get("BRD_DUMP_DIR", "/workspace/brd_dumps")

SIGS = {
    "cf_turnstile": [
        r"challenges\.cloudflare\.com",
        r"cf-challenge",
        r"turnstile",
        r"__cf_chl",
        r"cloudflare",
    ],
    "datadome": [
        r"datadome",
        r"dd_captcha",
        r"geo\.datadome",
    ],
    "perimeterx": [
        r"perimeterx",
        r"px-captcha",
        r"px-block",
        r"_px3",
        r"_pxhd",
        r"_pxvid",
    ],
    "recaptcha": [
        r"google\.com/recaptcha",
        r"g-recaptcha",
        r"recaptcha",
    ],
    "hcaptcha": [
        r"hcaptcha\.com",
        r"h-captcha",
        r"hcaptcha",
    ],
    "akamai": [
        r"akamai",
        r"bot manager",
        r"reference #",
    ],
    "blocked_429": [
        r"\b429\b",
        r"too many requests",
        r"rate limit",
    ],
    "access_denied": [
        r"access denied",
        r"request blocked",
        r"forbidden",
        r"\b403\b",
    ],
}

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def tag_html(text: str) -> List[str]:
    tags: List[str] = []
    for tag, patterns in SIGS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                tags.append(tag)
                break
    if not tags:
        tags = ["unknown"]
    return tags


def extract_title(text: str) -> str:
    m = TITLE_RE.search(text)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:160]


def main():
    files = sorted(glob.glob(str(Path(DUMP_DIR) / "*_captcha_*.html")))
    if not files:
        print(f"No *_captcha_*.html found in {DUMP_DIR}")
        return

    counts: Counter[str] = Counter()
    examples: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)

    for f in files:
        try:
            raw = Path(f).read_text(errors="ignore")
        except Exception:
            counts["read_error"] += 1
            continue

        tags = tag_html(raw)
        title = extract_title(raw)

        for tag in tags:
            counts[tag] += 1
            if len(examples[tag]) < 5:
                examples[tag].append((os.path.basename(f), title))

    print("=== SUMMARY ===")
    tagged_total = sum(v for k, v in counts.items() if k != "read_error")
    print(f"files={len(files)} tagged_total={tagged_total} read_error={counts.get('read_error', 0)}")
    for k, v in counts.most_common():
        print(f"{k}: {v}")

    print("\n=== EXAMPLES (up to 5 each) ===")
    for tag, ex in examples.items():
        print(f"\n[{tag}]")
        for name, title in ex:
            print(f"- {name} | title={title!r}")


if __name__ == "__main__":
    main()
