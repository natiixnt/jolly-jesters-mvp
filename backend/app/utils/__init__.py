from app.utils.excel_reader import ParsedRow, read_input_file  # noqa: F401
from app.utils.excel_writer import build_analysis_workbook  # noqa: F401


def normalize_ean(raw_ean: str | None) -> str | None:
    if raw_ean is None:
        return None
    digits = "".join(ch for ch in str(raw_ean).strip() if ch.isdigit())
    digits = digits.lstrip("0")
    return digits or None
