"""CSV, Excel, and PDF renderers for HR reports. Excel is a minimal spreadsheet
so the API does not take an extra dependency."""

from __future__ import annotations

import csv
import io
import zipfile
from xml.sax.saxutils import escape

from src.modules.hr.text_pdf import build_text_pdf


def to_csv(headers: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _column(index: int) -> str:
    label = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label


def to_xlsx(headers: list[str], rows: list[list]) -> bytes:
    def cell(ref: str, value) -> str:
        text = "" if value is None else str(value)
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'

    body = ['<row r="1">' + "".join(cell(f"{_column(i)}1", header) for i, header in enumerate(headers)) + "</row>"]
    for row_number, row in enumerate(rows, start=2):
        body.append(
            f'<row r="{row_number}">'
            + "".join(cell(f"{_column(i)}{row_number}", value) for i, value in enumerate(row))
            + "</row>"
        )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    package = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
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
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("_rels/.rels", package)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", rels)
        book.writestr("xl/worksheets/sheet1.xml", sheet)
    return blob.getvalue()


def to_pdf(title: str, headers: list[str], rows: list[list]) -> bytes:
    lines = [title, ""]
    if not rows:
        lines.append("No rows.")
    else:
        lines.append(" | ".join(headers))
        for row in rows:
            lines.append(" | ".join("" if value is None else str(value) for value in row))
    return build_text_pdf(lines)
