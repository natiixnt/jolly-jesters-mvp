from io import BytesIO

import pandas as pd

from app.utils.excel_reader import read_excel_file


def _make_excel_bytes(rows):
    df = pd.DataFrame(rows, columns=["EAN", "Name", "PurchasePrice"])
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()


def test_read_excel_file_valid_and_invalid_rows():
    data = _make_excel_bytes(
        [
            ["1234567890123", "Prod A", "100"],
            ["", "No EAN", "50"],
            ["2222222222222", "Bad price", "-1"],
        ]
    )

    rows = read_excel_file(data)
    assert len(rows) == 3
    valid_rows = [r for r in rows if r.is_valid]
    invalid_rows = [r for r in rows if not r.is_valid]

    assert len(valid_rows) == 1
    assert valid_rows[0].ean == "1234567890123"
    assert len(invalid_rows) == 2


def test_missing_columns_raises():
    df = pd.DataFrame([["123"]], columns=["ean_only"])
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)

    try:
        read_excel_file(buffer.getvalue())
        assert False, "expected ValueError"
    except ValueError:
        assert True
