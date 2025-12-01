from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional

import pandas as pd


@dataclass
class ParsedRow:
    row_number: int
    ean: str
    name: str
    purchase_price: Decimal


def _normalize_ean(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw).strip() if ch.isdigit())
    digits = digits.lstrip("0")
    return digits or None


def _parse_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        cleaned = str(value).strip().replace(",", ".")
        cleaned = "".join(ch for ch in cleaned if (ch.isdigit() or ch == "." or ch == "-"))
        if cleaned in {"", "-"}:
            return None
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _detect_column(columns, keys) -> Optional[str]:
    for col in columns:
        if any(key in str(col).lower() for key in keys):
            return col
    return None


def read_input_file(path: Path) -> List[ParsedRow]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    loader = pd.read_excel if path.suffix.lower() in {".xls", ".xlsx"} else pd.read_csv
    df = loader(path, dtype=str)
    df.columns = [str(c).lower() for c in df.columns]

    ean_col = _detect_column(df.columns, ["ean", "barcode", "kod"])
    name_col = _detect_column(df.columns, ["name", "nazwa", "title"])
    price_col = _detect_column(df.columns, ["price", "cena", "purchase", "netto", "cost"])

    if not ean_col or not price_col:
        raise ValueError("Missing required columns (EAN and price) in uploaded file")

    rows: List[ParsedRow] = []
    for idx, row in df.iterrows():
        ean = _normalize_ean(row.get(ean_col))
        price = _parse_decimal(row.get(price_col))
        if not ean or price is None:
            continue
        name = str(row.get(name_col) or "").strip() if name_col else ""
        rows.append(
            ParsedRow(
                row_number=int(idx) + 1,
                ean=ean,
                name=name,
                purchase_price=price,
            )
        )

    if not rows:
        raise ValueError("No valid rows found in the uploaded file")

    return rows
