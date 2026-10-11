"""Read a pay sheet and decide which rows can be imported.

Parsing uses the standard library. Header synonyms cover Tally pay sheets.
A model may suggest mappings afterwards; only an allow-listed field is kept,
and every amount is checked again here.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
import zlib
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from xml.etree import ElementTree

from src.modules.payroll.ops import rows_xlsx
from src.modules.payroll.text import ACCOUNT, IFSC, PAN

_MONEY = Decimal("0.01")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_OLE = bytes.fromhex("d0cf11e0a1b11ae1")

FIELDS = (
    "employee_code",
    "full_name",
    "pan",
    "uan",
    "pf_number",
    "esi_number",
    "account_number",
    "ifsc",
    "bank_name",
    "account_holder",
    "email",
    "phone",
    "ytd_gross",
    "ytd_tds",
    "ytd_pf",
    "ytd_esi",
    "ytd_pt",
)
YTD_FIELDS = ("ytd_gross", "ytd_tds", "ytd_pf", "ytd_esi", "ytd_pt")
CANONICAL = {"BASIC", "HRA", "DA"}

_SYNONYMS = {
    "employee no": "employee_code",
    "employee number": "employee_code",
    "emp no": "employee_code",
    "emp code": "employee_code",
    "employee code": "employee_code",
    "staff code": "employee_code",
    "staff no": "employee_code",
    "code": "employee_code",
    "name of employee": "full_name",
    "employee name": "full_name",
    "staff name": "full_name",
    "name": "full_name",
    "full name": "full_name",
    "pan": "pan",
    "pan no": "pan",
    "pan number": "pan",
    "permanent account number": "pan",
    "uan": "uan",
    "uan no": "uan",
    "uan number": "uan",
    "pf no": "pf_number",
    "pf number": "pf_number",
    "pf a c": "pf_number",
    "pf account": "pf_number",
    "esi no": "esi_number",
    "esi number": "esi_number",
    "ip number": "esi_number",
    "a c no": "account_number",
    "a c number": "account_number",
    "account no": "account_number",
    "account number": "account_number",
    "bank account": "account_number",
    "bank a c": "account_number",
    "account": "account_number",
    "ifs code": "ifsc",
    "ifsc": "ifsc",
    "ifsc code": "ifsc",
    "bank name": "bank_name",
    "bank": "bank_name",
    "account holder": "account_holder",
    "beneficiary": "account_holder",
    "beneficiary name": "account_holder",
    "email": "email",
    "e mail": "email",
    "phone": "phone",
    "mobile": "phone",
    "mobile no": "phone",
    "ytd earnings": "ytd_gross",
    "ytd gross": "ytd_gross",
    "opening gross": "ytd_gross",
    "ytd tds": "ytd_tds",
    "opening tds": "ytd_tds",
    "ytd pf": "ytd_pf",
    "opening pf": "ytd_pf",
    "ytd esi": "ytd_esi",
    "opening esi": "ytd_esi",
    "ytd pt": "ytd_pt",
    "opening pt": "ytd_pt",
    "basic salary": "BASIC",
    "basic pay": "BASIC",
    "basic": "BASIC",
    "house rent allowance": "HRA",
    "hra": "HRA",
    "dearness allowance": "DA",
    "da": "DA",
}
_IGNORED = {
    "pf",
    "esi",
    "pt",
    "tds",
    "gross",
    "net",
    "gross salary",
    "net salary",
    "net pay",
    "net amount",
    "gross amount",
    "total deduction",
    "total deductions",
    "deductions",
    "deduction",
    "lop",
    "lop days",
    "working days",
    "paid days",
    "designation",
    "department",
    "sr no",
    "s no",
    "sl no",
    "sno",
    "serial",
    "serial no",
    "month",
    "date of joining",
    "doj",
    "employee pf",
    "employer pf",
    "pf employee",
    "esi employee",
    "professional tax",
    "income tax",
}

TALLY_SAMPLE_HEADERS = [
    "Name of Employee",
    "Employee No.",
    "PAN",
    "A/c No.",
    "IFS Code",
    "Basic Salary",
    "House Rent Allowance",
    "Dearness Allowance",
    "YTD Earnings",
    "YTD TDS",
    "PF",
    "Gross",
    "Bonus",
]
TALLY_SAMPLE_ROWS = [
    ["Asha Rao", "E001", "ABCDE1234F", "123456789012", "HDFC0001234", "30000", "12000", "5000", "180000", "12000", "1800", "47000", ""],
    ["Missing Pan", "E002", "", "123456789013", "HDFC0001234", "20000", "8000", "0", "0", "0", "0", "28000", ""],
    ["Bad Ifsc", "E003", "ABCDE1234G", "123456789014", "HDFC1001234", "20000", "8000", "0", "0", "0", "0", "28000", ""],
    ["Asha Again", "E001", "ABCDE1234F", "123456789012", "HDFC0001234", "30000", "12000", "5000", "0", "0", "0", "47000", ""],
    ["Unknown Head", "E004", "ABCDE1234H", "123456789015", "HDFC0001234", "20000", "8000", "0", "0", "0", "0", "28000", "5000"],
    ["Bad Amount", "E005", "ABCDE1234J", "123456789016", "HDFC0001234", "twelve", "8000", "0", "0", "0", "0", "28000", ""],
]


class SheetError(ValueError):
    pass


class _Unreadable(ValueError):
    pass


def tally_sample_xlsx() -> bytes:
    return rows_xlsx(TALLY_SAMPLE_HEADERS, TALLY_SAMPLE_ROWS)


def allow_list(known: set[str]) -> set[str]:
    return set(FIELDS) | {code.upper() for code in known} | set(CANONICAL)


def normalize_header(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def header_target(header: str, known: set[str]) -> str | None:
    """A field name, IGNORE, or None when the column is not recognised."""
    key = normalize_header(header)
    if not key:
        return "IGNORE"
    if key in _SYNONYMS:
        return _SYNONYMS[key]
    if key in _IGNORED:
        return "IGNORE"
    compact = key.replace(" ", "").upper()
    if compact in known or compact in CANONICAL:
        return compact
    return None


def deterministic_mapping(headers: list[str], known: set[str]) -> tuple[dict[str, str], list[str]]:
    mapping: dict[str, str] = {}
    unknown: list[str] = []
    used: set[str] = set()
    for header in headers:
        target = header_target(header, known)
        if target is None:
            unknown.append(header)
        elif target != "IGNORE" and target not in used:
            mapping[header] = target
            used.add(target)
    return mapping, unknown


def merge_mapping(mapping: dict[str, str], proposed: dict, allow: set[str]) -> dict[str, str]:
    """Keep a model suggestion only when the field is on the allow-list and still free."""
    merged = dict(mapping)
    used = set(merged.values())
    for header, choice in (proposed or {}).items():
        field = str(choice or "")
        if header in merged or field not in allow or field in used:
            continue
        merged[str(header)] = field
        used.add(field)
    return merged


def parse_sheet(content: bytes, filename: str) -> tuple[list[str], list[list[str]]]:
    kind = _kind(filename, content)
    if kind == "xlsx":
        grid = _xlsx_rows(content)
    elif kind == "xls":
        grid = _xls_rows(content)
    elif kind == "pdf":
        grid = _pdf_rows(content)
    else:
        grid = _csv_rows(content)
    if len(grid) < 2:
        raise SheetError("The sheet needs a header row and at least one data row")
    headers = [str(cell).strip() for cell in grid[0]]
    if not any(headers):
        raise SheetError("The header row is empty")
    width = len(headers)
    matrix = []
    for raw in grid[1:]:
        cells = [str(cell).strip() for cell in raw]
        if len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        cells = cells[:width]
        if any(cells):
            matrix.append(cells)
    if not matrix:
        raise SheetError("The sheet has no data rows")
    return headers, matrix


def validate_sheet(headers: list[str], matrix: list[list[str]], mapping: dict[str, str], known: set[str]) -> dict:
    field_at: dict[str, int] = {}
    for index, header in enumerate(headers):
        field = mapping.get(header)
        if field and field not in field_at:
            field_at[field] = index
    unknown_at = []
    for index, header in enumerate(headers):
        if not header.strip() or header in mapping:
            continue
        if header_target(header, known) == "IGNORE":
            continue
        unknown_at.append(index)
    accepted = []
    rejected = []
    seen: set[str] = set()
    for offset, cells in enumerate(matrix):
        row_number = offset + 2
        issues = []

        def take(field: str) -> tuple[str, str]:
            index = field_at.get(field)
            if index is None or index >= len(cells):
                return "", field
            return cells[index], headers[index]

        code, _code_header = take("employee_code")
        name, name_header = take("full_name")
        pan, pan_header = take("pan")
        if not code:
            issues.append(_issue(row_number, code, "employee_code", "missing employee code", code))
        elif len(code) > 64:
            issues.append(_issue(row_number, code, "employee_code", f"unreadable cell: {_code_header}", code))
        elif code.lower() in seen:
            issues.append(_issue(row_number, code, "employee_code", "duplicate code", code))
        if code:
            seen.add(code.lower())
        if not name:
            issues.append(_issue(row_number, code, "full_name", "missing employee name", name))
        elif len(name) > 200:
            issues.append(_issue(row_number, code, "full_name", f"unreadable cell: {name_header}", name))
        if not pan:
            issues.append(_issue(row_number, code, "pan", "missing PAN", pan))
        elif not PAN.match(pan.upper()):
            issues.append(_issue(row_number, code, "pan", f"unreadable cell: {pan_header}", pan))

        account, account_header = take("account_number")
        ifsc, _ifsc_header = take("ifsc")
        bank_name, _bank_header = take("bank_name")
        holder, _holder_header = take("account_holder")
        bank = None
        if any((account, ifsc, bank_name, holder)):
            digits = "".join(ch for ch in account if ch.isdigit())
            if not IFSC.match(ifsc.strip().upper()):
                issues.append(_issue(row_number, code, "ifsc", "invalid IFSC", ifsc))
            if not ACCOUNT.match(digits):
                label = account_header if account_header != "account_number" else "A/c No."
                issues.append(_issue(row_number, code, "account_number", f"unreadable cell: {label}", account))
            if IFSC.match(ifsc.strip().upper()) and ACCOUNT.match(digits):
                bank = {
                    "account_holder": (holder or name)[:120],
                    "account_number": digits,
                    "ifsc": ifsc.strip().upper(),
                    "bank_name": (bank_name or "Bank")[:120],
                }

        components: dict[str, str] = {}
        ytd = {field: "0.00" for field in YTD_FIELDS}
        for field, index in field_at.items():
            if field in YTD_FIELDS or _is_component(field):
                raw = cells[index] if index < len(cells) else ""
                header = headers[index]
                try:
                    amount = _money(raw)
                except _Unreadable:
                    issues.append(_issue(row_number, code, field, f"unreadable cell: {header}", raw))
                    continue
                if amount is None:
                    continue
                rendered = str(amount)
                if field in YTD_FIELDS:
                    ytd[field] = rendered
                else:
                    components[field] = rendered
        for index in unknown_at:
            raw = cells[index].strip() if index < len(cells) else ""
            if raw:
                issues.append(_issue(row_number, code, headers[index], f"unknown component: {headers[index]}", raw))
        email, email_header = take("email")
        phone, phone_header = take("phone")
        if email and ("@" not in email or len(email) > 320):
            issues.append(_issue(row_number, code, "email", f"unreadable cell: {email_header}", email))
        if phone and len(phone) > 32:
            issues.append(_issue(row_number, code, "phone", f"unreadable cell: {phone_header}", phone))
        uan, _ = take("uan")
        pf_number, _ = take("pf_number")
        esi_number, _ = take("esi_number")
        if issues:
            rejected.extend(issues)
            continue
        accepted.append(
            {
                "row": row_number,
                "action": "create",
                "employee_code": code,
                "full_name": name,
                "pan": pan.upper(),
                "uan": uan[:32],
                "pf_number": pf_number[:32],
                "esi_number": esi_number[:32],
                "email": email,
                "phone": phone,
                "bank": bank,
                "components": components,
                "ytd": ytd,
            }
        )
    return {"mapping": mapping, "accepted": accepted, "rejected": rejected}


def _is_component(field: str) -> bool:
    return field not in FIELDS


def _issue(row: int, code: str, field: str, reason: str, value: str) -> dict:
    return {"row": row, "employee_code": code, "field": field, "reason": reason, "value": value}


def _money(value: str) -> Decimal | None:
    text = (value or "").strip()
    if text in {"", "-", "—"}:
        return None
    text = text.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise _Unreadable(value)
    try:
        amount = Decimal(text).quantize(_MONEY)
    except InvalidOperation as exc:
        raise _Unreadable(value) from exc
    if amount > Decimal("100000000"):
        raise _Unreadable(value)
    return amount


def _kind(filename: str, content: bytes) -> str:
    name = (filename or "").lower()
    for ext in ("xlsx", "xls", "csv", "pdf"):
        if name.endswith("." + ext):
            return ext
    if content.startswith(b"PK\x03\x04"):
        return "xlsx"
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(_OLE):
        return "xls"
    head = content[:1500].lstrip().lower()
    if head.startswith(b"<") or b"<table" in head:
        return "xls"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SheetError("Upload an xlsx, xls, csv, or pdf file") from exc
    return "csv"


def _xlsx_rows(content: bytes) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise SheetError("This xlsx workbook could not be read") from exc
    with archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{_NS}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{_NS}t")))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in names:
            sheets = sorted(name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
            if not sheets:
                raise SheetError("This xlsx workbook has no worksheet")
            sheet_name = sheets[0]
        root = ElementTree.fromstring(archive.read(sheet_name))
    grid: dict[int, dict[int, str]] = {}
    for cell in root.iter(f"{_NS}c"):
        ref = cell.attrib.get("r") or ""
        column = _column(ref)
        row = _row_number(ref)
        if column is None or row is None:
            continue
        kind = cell.attrib.get("t")
        value = ""
        if kind == "inlineStr":
            value = "".join(node.text or "" for node in cell.iter(f"{_NS}t"))
        else:
            node = cell.find(f"{_NS}v")
            raw = node.text if node is not None and node.text else ""
            if kind == "s" and raw.isdigit():
                index = int(raw)
                value = shared[index] if index < len(shared) else ""
            else:
                value = raw
        grid.setdefault(row, {})[column] = value
    if not grid:
        raise SheetError("This xlsx workbook has no cells")
    return _dense(grid)


def _xls_rows(content: bytes) -> list[list[str]]:
    head = content[:4000].lstrip().lower()
    if head.startswith(b"<") or b"<table" in head or b"<html" in head:
        return _html_rows(content)
    if head.startswith(b"<?xml") or b"<workbook" in head or b"spreadsheetml" in head:
        return _spreadsheetml_rows(content)
    if content.startswith(_OLE) or b"<table" in content[:20000].lower():
        at = content.lower().find(b"<table")
        if at >= 0:
            return _html_rows(content[at:])
    raise SheetError("This .xls workbook could not be read. Save it as xlsx or csv.")


def _csv_rows(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252", errors="replace")
    first = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if first.count("\t") > first.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(cell.strip() for cell in row)]


def _html_rows(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8", errors="replace")
    parser = _TableParser()
    parser.feed(text)
    if not parser.tables:
        raise SheetError("This sheet has no table")
    return max(parser.tables, key=len)


def _spreadsheetml_rows(content: bytes) -> list[list[str]]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise SheetError("This spreadsheet could not be read") from exc
    rows = []
    for row in root.iter():
        if not str(row.tag).endswith("Row"):
            continue
        cells = []
        for cell in list(row):
            if not str(cell.tag).endswith("Cell"):
                continue
            value = ""
            for node in cell.iter():
                if str(node.tag).endswith("Data") and node.text:
                    value = node.text
            cells.append(value.strip())
        if any(cells):
            rows.append(cells)
    if not rows:
        raise SheetError("This spreadsheet has no rows")
    return rows


def _pdf_rows(content: bytes) -> list[list[str]]:
    blob = _pdf_bytes(content)
    rows = []
    for chunk in re.split(br"T\*|ET", blob):
        cells = [_pdf_literal(match.group(0)) for match in re.finditer(br"\((?:\\.|[^\\)])*\)", chunk)]
        cells = [cell.strip() for cell in cells if cell.strip()]
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        text = " ".join(cell for row in rows for cell in row)
        lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
        if len(lines) >= 2:
            return [re.split(r"\s{2,}|\t|,", line) for line in lines]
        raise SheetError("This PDF has no readable table")
    return rows


def _pdf_bytes(content: bytes) -> bytes:
    parts = []
    for match in re.finditer(br"stream\r?\n(.*?)\r?\nendstream", content, re.S):
        raw = match.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        parts.append(raw)
    return b"\n".join(parts) if parts else content


def _pdf_literal(token: bytes) -> str:
    inner = token[1:-1]
    out = bytearray()
    index = 0
    while index < len(inner):
        if inner[index] == 92 and index + 1 < len(inner):
            nxt = inner[index + 1]
            mapped = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("("): 40, ord(")"): 41, ord("\\"): 92}
            if nxt in mapped:
                out.append(mapped[nxt])
                index += 2
                continue
        out.append(inner[index])
        index += 1
    return out.decode("latin-1", errors="replace")


def _column(ref: str) -> int | None:
    letters = "".join(ch for ch in ref if ch.isalpha())
    if not letters:
        return None
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch.upper()) - 64)
    return index


def _row_number(ref: str) -> int | None:
    digits = "".join(ch for ch in ref if ch.isdigit())
    return int(digits) if digits else None


def _dense(grid: dict[int, dict[int, str]]) -> list[list[str]]:
    last_row = max(grid)
    last_col = max(col for row in grid.values() for col in row)
    table = []
    for row in range(1, last_row + 1):
        cells = grid.get(row) or {}
        table.append([cells.get(col, "") for col in range(1, last_col + 1)])
    return table


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._depth == 0:
                self._rows = []
            self._depth += 1
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._rows is not None:
            if any(self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            if self._depth == 0 and self._rows:
                self.tables.append(self._rows)
                self._rows = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
