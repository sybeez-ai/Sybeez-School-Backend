"""CSV, PDF and Excel exports for finance reports."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

from src.modules.reports.exporters import rows_to_csv, rows_to_pdf


def rows_to_xlsx(title: str, rows: list[dict], columns: list[str]) -> bytes:
    """Minimal Office Open XML workbook. Amounts stay text so Excel does not float them."""

    def cell(ref: str, value: str) -> str:
        return (
            f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        )

    header = "".join(cell(f"{_col(i)}1", columns[i]) for i in range(len(columns)))
    body_rows = [f'<row r="1">{header}</row>']
    for r_i, row in enumerate(rows, start=2):
        cells = "".join(
            cell(f"{_col(c_i)}{r_i}", "" if row.get(columns[c_i]) is None else str(row.get(columns[c_i])))
            for c_i in range(len(columns))
        )
        body_rows.append(f'<row r="{r_i}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body_rows)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(title[:31] or "Report")}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _col(index: int) -> str:
    name = ""
    n = index
    while True:
        name = chr(ord("A") + (n % 26)) + name
        n = n // 26 - 1
        if n < 0:
            break
    return name


__all__ = ["rows_to_csv", "rows_to_pdf", "rows_to_xlsx"]
