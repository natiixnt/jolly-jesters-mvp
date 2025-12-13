from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import Dict, List, Optional

import pandas as pd


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
SUPPORTED_SYMBOLS = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
}


@dataclass
class InputRow:
    row_number: int
    ean: str
    name: str
    original_purchase_price: Optional[Decimal]
    purchase_price_pln: Optional[Decimal]
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


def _detect_currency_from_header(header: str) -> Optional[str]:
    header_norm = _norm(str(header))
    if "eur" in header_norm or "€" in header_norm:
        return "EUR"
    if "usd" in header_norm:
        return "USD"
    if "cad" in header_norm:
        return "CAD"
    if "pln" in header_norm or "zl" in header_norm:
        return "PLN"
    return None


def _detect_currency_from_value(value: object) -> tuple[Optional[str], Optional[str]]:
    raw = "" if value is None else str(value)
    lowered = raw.lower()
    if "pln" in lowered or "zł" in lowered or "zl" in lowered:
        return "PLN", raw
    if "cad" in lowered:
        return "CAD", raw
    if "eur" in lowered or "€" in raw:
        return "EUR", raw
    if "$" in raw:
        return "USD", raw
    return None, raw


def _parse_price(raw: object) -> Optional[Decimal]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw)
    for sym in SUPPORTED_SYMBOLS:
        text = text.replace(sym, "")
    text = (
        text.replace("PLN", "")
        .replace("pln", "")
        .replace("zł", "")
        .replace("zl", "")
        .replace("CAD", "")
        .replace("USD", "")
        .replace("EUR", "")
        .replace(" ", "")
    )
    text = text.replace(",", ".")
    try:
        return Decimal(text)
    except Exception:
        return None


def read_excel_file(
    file_bytes: bytes,
    currency_rates: Dict[str, float],
    default_currency: Optional[str] = None,
) -> List[InputRow]:
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

    normalized_rates: Dict[str, Decimal] = {}
    for code, rate in currency_rates.items():
        code_up = str(code).upper()
        try:
            rate_val = Decimal(str(rate))
        except Exception:
            continue
        if rate_val <= 0:
            continue
        normalized_rates[code_up] = rate_val

    if "PLN" not in normalized_rates or normalized_rates["PLN"] != Decimal("1"):
        normalized_rates["PLN"] = Decimal("1")

    rows: List[InputRow] = []
    for idx, row in df.iterrows():
        raw_ean = row.iloc[ean_idx]
        raw_name = row.iloc[name_idx]
        raw_price = row.iloc[price_idx]

        if pd.isna(raw_ean) and pd.isna(raw_name):
            continue

        ean_digits = "".join(ch for ch in str(raw_ean) if ch.isdigit())
        name = "" if pd.isna(raw_name) else str(raw_name).strip()

        header_currency = _detect_currency_from_header(df.columns[price_idx])
        value_currency, _ = _detect_currency_from_value(raw_price)
        currency = (value_currency or header_currency)
        if not currency:
            if default_currency:
                currency = default_currency
            else:
                raise ValueError(
                    "Nie udalo sie wykryc waluty w pliku, a brak waluty domyslnej. "
                    "Ustaw walute domyslna w panelu kursow."
                )
        currency = currency.upper()
        price_value = _parse_price(raw_price)

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
            if currency not in normalized_rates:
                raise ValueError(
                    f"Nieznana waluta '{currency}'. Dodaj kurs w ustawieniach i spróbuj ponownie."
                )
            rate = normalized_rates[currency]
            price_pln = price_value * rate

        rows.append(
            InputRow(
                row_number=header_idx + idx + 2,
                ean=ean_digits,
                name=name,
                original_purchase_price=price_value,
                purchase_price_pln=price_pln,
                purchase_currency=currency,
                is_valid=is_valid,
                error=error,
            )
        )

    return rows
