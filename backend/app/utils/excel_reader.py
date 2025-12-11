from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import List, Optional

import pandas as pd

from app.core.config import settings


def _norm(s: str) -> str:
    return (
        s.strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


EAN_HEADERS = {"ean", "ean13", "barcode", "kodeskreskowy", "kodean"}
NAME_HEADERS = {"name", "productname", "product", "nazwa", "opis", "description"}
PRICE_HEADERS = {
    "purchaseprice",
    "buyprice",
    "cost",
    "cena",
    "cenazakupu",
    "netpurchase",
    "netprice",
    "price",
    "eurprice",
    "€price",
}
CURRENCY_HEADERS = {"currency", "waluta"}


@dataclass
class InputRow:
    row_number: int
    ean: str
    name: str
    purchase_price: Optional[Decimal]
    purchase_currency: str
    is_valid: bool
    error: Optional[str]


def _looks_like_ean_series(series: pd.Series) -> bool:
    vals = [str(v) for v in series.dropna().head(50)]
    if not vals:
        return False
    hits = 0
    for v in vals:
        digits = "".join(ch for ch in v if ch.isdigit())
        if 12 <= len(digits) <= 14:
            hits += 1
    return hits >= max(3, int(len(vals) * 0.6))


def _detect_header_row(df_raw: pd.DataFrame, max_scan: int = 20) -> int:
    header_tokens = (
        EAN_HEADERS
        | NAME_HEADERS
        | PRICE_HEADERS
        | CURRENCY_HEADERS
        | {"qty", "€price", "eurprice"}
    )

    for i in range(min(max_scan, len(df_raw))):
        row = df_raw.iloc[i]
        tokens = {_norm(str(v)) for v in row.dropna().tolist()}
        if not tokens:
            continue
        if tokens & header_tokens:
            return i
    return 0


def read_excel_file(file_bytes: bytes) -> List[InputRow]:
    df_raw = pd.read_excel(BytesIO(file_bytes), header=None)

    header_idx = _detect_header_row(df_raw)
    headers = df_raw.iloc[header_idx]
    df = df_raw.iloc[header_idx + 1 :].reset_index(drop=True)
    df.columns = headers

    ean_idx: Optional[int] = None
    name_idx: Optional[int] = None
    price_idx: Optional[int] = None
    currency_idx: Optional[int] = None

    for idx, col_name in enumerate(df.columns):
        if pd.isna(col_name):
            continue
        n = _norm(str(col_name))
        if ean_idx is None and n in EAN_HEADERS:
            ean_idx = idx
        if name_idx is None and n in NAME_HEADERS:
            name_idx = idx
        if price_idx is None and (n in PRICE_HEADERS or "price" in n):
            price_idx = idx
        if currency_idx is None and n in CURRENCY_HEADERS:
            currency_idx = idx

    n_cols = df.shape[1]

    if ean_idx is None:
        for idx in range(n_cols):
            series = df.iloc[:, idx]
            if _looks_like_ean_series(series):
                ean_idx = idx
                break

    if name_idx is None:
        candidate_idx: Optional[int] = None
        for idx in range(n_cols):
            if idx in {ean_idx, price_idx, currency_idx}:
                continue
            series = df.iloc[:, idx]
            non_null = series.dropna().head(50)
            if non_null.empty:
                continue
            str_vals = [v for v in non_null if isinstance(v, str)]
            if not str_vals:
                continue
            avg_len = sum(len(v) for v in str_vals) / len(str_vals)
            if avg_len > 15:
                if candidate_idx is None:
                    candidate_idx = idx
                elif ean_idx is not None and abs(idx - ean_idx) < abs(candidate_idx - ean_idx):
                    candidate_idx = idx
        if candidate_idx is not None:
            name_idx = candidate_idx

    if price_idx is None:
        for idx in range(n_cols):
            if idx in {ean_idx, name_idx, currency_idx}:
                continue
            series = df.iloc[:, idx]
            non_null = [str(v) for v in series.dropna().head(50)]
            if not non_null:
                continue
            numeric_like = 0
            for v in non_null:
                v2 = v.replace(" ", "").replace("€", "").replace(",", ".")
                try:
                    float(v2)
                    numeric_like += 1
                except ValueError:
                    continue
            if numeric_like >= max(3, int(len(non_null) * 0.6)):
                price_idx = idx
                break

    if ean_idx is None or name_idx is None or price_idx is None:
        detected = [str(c) for c in df.columns.tolist()]
        raise ValueError(
            "Brak wymaganych kolumn (EAN, nazwa, cena zakupu) - sprawdz naglowki "
            f"lub uklad pliku. Wykryte naglowki: {detected}"
        )

    rows: List[InputRow] = []
    for idx, row in df.iterrows():
        raw_ean = row.iloc[ean_idx]
        raw_name = row.iloc[name_idx]
        raw_price = row.iloc[price_idx]

        if pd.isna(raw_ean) and pd.isna(raw_name):
            continue

        ean_digits = "".join(ch for ch in str(raw_ean) if ch.isdigit())
        name = "" if pd.isna(raw_name) else str(raw_name).strip()

        price_str = str(raw_price).replace(" ", "").replace("€", "").replace(",", ".")
        try:
            price_value = float(price_str)
        except ValueError:
            price_value = None

        currency = "PLN"
        price_header = df.columns[price_idx]
        header_norm = _norm(str(price_header))
        if "eur" in header_norm or "€price" in header_norm:
            currency = "EUR"

        is_valid = True
        error = None

        if not ean_digits:
            is_valid = False
            error = "Brak EAN"
        elif price_value is None or price_value <= 0:
            is_valid = False
            error = "Nieprawidlowa cena zakupu"

        price_pln: Optional[Decimal] = None
        if price_value is not None and price_value > 0:
            if currency == "EUR":
                price_value *= settings.eur_to_pln_rate
            price_pln = Decimal(str(price_value))

        rows.append(
            InputRow(
                row_number=header_idx + idx + 2,
                ean=ean_digits,
                name=name,
                purchase_price=price_pln,
                purchase_currency=currency,
                is_valid=is_valid,
                error=error,
            )
        )

    return rows
