from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Iterable, Optional

import pandas as pd

from app.models.category import Category
from app.models.analysis_run_item import AnalysisRunItem
from app.services.analysis_service import serialize_analysis_item


def _status_label(status: Optional[object]) -> str:
    if status is None:
        return "—"
    value = getattr(status, "value", status)
    if value == "pending":
        return "pending local"
    if value == "in_progress":
        return "w trakcie"
    if value == "not_found":
        return "brak"
    if value == "blocked":
        return "blocked"
    if value == "network_error":
        return "błąd sieci"
    if value == "error":
        return "błąd"
    return "ok"


def _profitability_label(is_profitable: Optional[bool]) -> str:
    if is_profitable is True:
        return "tak"
    if is_profitable is False:
        return "nie"
    return "—"


def _format_original_price(value: Optional[float], currency: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if currency:
        return f"{value} {currency}"
    return str(value)


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = value
        if isinstance(value, str):
            return value
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def build_analysis_excel(items: Iterable[AnalysisRunItem], category: Optional[Category]) -> bytes:
    category_name = category.name if category else ""
    rows = []
    for item in items:
        result = serialize_analysis_item(item, category)
        rows.append(
            {
                "EAN": result.ean,
                "Nazwa": result.name,
                "Waluta": result.original_currency or "PLN",
                "Cena zakupu (oryg.)": _format_original_price(result.original_purchase_price, result.original_currency),
                "Cena zakupu (PLN)": result.purchase_price_pln,
                "Cena Allegro": result.allegro_price_pln,
                "Marża": result.margin_pln,
                "Marża %": result.margin_percent,
                "Sprzedanych": result.sold_count,
                "Opłacalny": _profitability_label(result.is_profitable),
                "Źródło": result.source or "—",
                "Ostatnio sprawdzono": _format_datetime(result.last_checked_at),
                "Status": _status_label(result.scrape_status),
                "Błąd scrapingu": result.scrape_error_message,
                "Kategoria": category_name,
            }
        )

    df = pd.DataFrame(rows)
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            if pd.api.types.is_datetime64tz_dtype(df[col]):
                df[col] = df[col].dt.tz_convert(None)
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")

    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()
