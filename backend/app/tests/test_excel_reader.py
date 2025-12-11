from io import BytesIO

import pandas as pd

from app.utils.excel_reader import read_excel_file


def _make_excel_bytes(rows):
    df = pd.DataFrame(rows, columns=["EAN", "Name", "PurchasePrice"])
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer.getvalue()


def _make_excel_bytes_no_header(rows):
    df = pd.DataFrame(rows)
    buffer = BytesIO()
    df.to_excel(buffer, index=False, header=False)
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
    assert valid_rows[0].purchase_currency == "PLN"
    assert len(invalid_rows) == 2


def test_eu_stock_like_headers_are_detected():
    rows = [
        ["ADOLFO DOMINGUEZ", None, None, None, None, "QTY", "€ PRICE"],
        ["brand", None, 1234567890123, "Perfum A very long name", None, 5, "35,50"],
        ["brand", None, 9876543210123, "Perfum B with long name", None, 2, "12.30"],
    ]
    data = _make_excel_bytes_no_header(rows)

    parsed = read_excel_file(data)
    assert len(parsed) == 2
    assert all(r.is_valid for r in parsed)
    assert parsed[0].ean == "1234567890123"
    assert parsed[0].name == "Perfum A"
    assert parsed[0].purchase_currency == "EUR"
    assert float(parsed[0].purchase_price) > 35  # converted to PLN with default rate


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
