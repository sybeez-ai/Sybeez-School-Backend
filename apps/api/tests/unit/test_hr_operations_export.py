"""Settlement arithmetic and report file renderers."""

import zipfile
from decimal import Decimal
from io import BytesIO

from src.modules.hr_operations.export import to_csv, to_pdf, to_xlsx
from src.modules.hr_operations.service import settlement_totals


def test_payable_floors_at_zero():
    dues, recoveries, payable = settlement_totals(
        [("DUE", Decimal("1000.005")), ("RECOVERY", Decimal("250")), ("RECOVERY", Decimal("900"))]
    )
    assert dues == Decimal("1000.01")
    assert recoveries == Decimal("1150.00")
    assert payable == Decimal("0.00")


def test_csv_and_xlsx_and_pdf_round_trip_headers():
    headers = ["name", "status"]
    rows = [["Ada", "ACTIVE"]]
    csv_text = to_csv(headers, rows).decode()
    assert csv_text.splitlines()[0] == "name,status"
    assert "Ada,ACTIVE" in csv_text

    book = zipfile.ZipFile(BytesIO(to_xlsx(headers, rows)))
    sheet = book.read("xl/worksheets/sheet1.xml").decode()
    assert "Ada" in sheet and "ACTIVE" in sheet

    pdf = to_pdf("Headcount", headers, rows)
    assert pdf.startswith(b"%PDF")
