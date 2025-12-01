from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import List, Optional

import pandas as pd


@dataclass
class InputRow:
    row_number: int
    ean: str
    name: str
    purchase_price: Optional[Decimal]
    is_valid: bool
    error: Optional[str]


def _normalize_column(col: str) -> str:
    return str(col).strip().lower().replace(" ", "").replace("_", "")


def _parse_price(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    raw = str(value).strip().replace(",", ".")
    try:
        cleaned = "".join(ch for ch in raw if ch.isdigit() or ch == "." or ch == "-")
        if cleaned in {"", "-"}:
            return None
        dec = Decimal(cleaned)
        return dec
    except (InvalidOperation, ValueError):
        return None


def read_excel_file(file_bytes: bytes) -> List[InputRow]:
    try:
        df = pd.read_excel(BytesIO(file_bytes), dtype=str)
    except Exception as exc:
        raise ValueError(f"Nie udało się wczytać pliku Excel: {exc}")

    normalized_cols = {_normalize_column(c): c for c in df.columns}
    required = {"ean", "name", "purchaseprice"}
    if not required.issubset(normalized_cols.keys()):
        missing = required - set(normalized_cols.keys())
        raise ValueError(f"Brak wymaganych kolumn: {', '.join(sorted(missing))}")

    ean_col = normalized_cols["ean"]
    name_col = normalized_cols["name"]
    price_col = normalized_cols["purchaseprice"]

    rows: List[InputRow] = []
    for idx, row in df.iterrows():
        ean_raw = (row.get(ean_col) or "").strip()
        name_raw = (row.get(name_col) or "").strip()
        price_raw = row.get(price_col)

        ean = ean_raw
        price = _parse_price(price_raw)

        is_valid = True
        error = None

        if not ean:
            is_valid = False
            error = "Brak EAN"
        elif price is None or price <= 0:
            is_valid = False
            error = "Nieprawidłowa cena zakupu"

        rows.append(
            InputRow(
                row_number=int(idx) + 2,  # include header row offset
                ean=ean,
                name=name_raw,
                purchase_price=price,
                is_valid=is_valid,
                error=error,
            )
        )

    return rows
