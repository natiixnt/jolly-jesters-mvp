from __future__ import annotations

from io import BytesIO
from typing import Iterable

import pandas as pd

from app.models.analysis_run_item import AnalysisRunItem


def build_analysis_excel(items: Iterable[AnalysisRunItem], category_name: str) -> bytes:
    def resolve_source(item: AnalysisRunItem) -> str | None:
        base = getattr(item.source, "value", item.source)
        product = getattr(item, "product", None)
        if product and getattr(product, "effective_state", None) and product.effective_state.last_market_data:
            md = product.effective_state.last_market_data
            raw_source = None
            try:
                raw_source = (md.raw_payload or {}).get("source")
            except Exception:
                raw_source = None
            if raw_source:
                return raw_source
            if md.source:
                return getattr(md.source, "value", md.source)
        return base

    data = []
    for item in items:
        label = getattr(item.profitability_label, "value", item.profitability_label)
        source = getattr(item.source, "value", item.source)
        source_label = resolve_source(item) or source
        last_checked_at = None
        try:
            last_checked_at = (
                item.product.effective_state.last_checked_at  # type: ignore[attr-defined]
                if item.product and getattr(item.product, "effective_state", None)
                else None
            )
        except Exception:
            last_checked_at = None
        data.append(
            {
                "row_number": item.row_number,
                "category_name": category_name,
                "ean": item.ean,
                "input_name": item.input_name,
                "input_purchase_price": float(item.input_purchase_price) if item.input_purchase_price else None,
                "source": source_label or source,
                "allegro_price": float(item.allegro_price) if item.allegro_price else None,
                "allegro_sold_count": item.allegro_sold_count,
                "profitability_score": float(item.profitability_score) if item.profitability_score else None,
                "profitability_label": label,
                "error_message": item.error_message,
                "last_checked_at": last_checked_at,
            }
        )

    df = pd.DataFrame(data)
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()
