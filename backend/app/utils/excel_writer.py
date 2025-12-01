from __future__ import annotations

from io import BytesIO
from typing import Iterable

import pandas as pd

from app.models.analysis_run_item import AnalysisRunItem


def build_analysis_excel(items: Iterable[AnalysisRunItem], category_name: str) -> bytes:
    data = []
    for item in items:
        label = getattr(item.profitability_label, "value", item.profitability_label)
        source = getattr(item.source, "value", item.source)
        data.append(
            {
                "row_number": item.row_number,
                "category_name": category_name,
                "ean": item.ean,
                "input_name": item.input_name,
                "input_purchase_price": float(item.input_purchase_price) if item.input_purchase_price else None,
                "source": source,
                "allegro_price": float(item.allegro_price) if item.allegro_price else None,
                "allegro_sold_count": item.allegro_sold_count,
                "profitability_score": float(item.profitability_score) if item.profitability_score else None,
                "profitability_label": label,
                "error_message": item.error_message,
            }
        )

    df = pd.DataFrame(data)
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()
