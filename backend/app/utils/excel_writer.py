from __future__ import annotations

from io import BytesIO
from typing import Iterable

import pandas as pd

from app.models.analysis_run_item import AnalysisRunItem


def build_analysis_workbook(items: Iterable[AnalysisRunItem]) -> BytesIO:
    data = []
    for item in items:
        source = item.source.value if hasattr(item.source, "value") else item.source
        label = (
            item.profitability_label.value
            if hasattr(item.profitability_label, "value")
            else item.profitability_label
        )
        data.append(
            {
                "Wiersz": item.row_number,
                "EAN": item.ean,
                "Nazwa": item.input_name,
                "Cena zakupu": float(item.input_purchase_price) if item.input_purchase_price else None,
                "Cena Allegro": float(item.allegro_price) if item.allegro_price else None,
                "Sprzedane sztuki": item.allegro_sold_count,
                "Ocena": label if label else None,
                "Score": float(item.profitability_score) if item.profitability_score else None,
                "Źródło": source,
                "Błąd": item.error_message,
            }
        )

    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return output
