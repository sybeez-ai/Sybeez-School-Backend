"""Pure helpers for the daily payroll desk: schedules, files, imports, and pages."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from src.core.exceptions import AppError
from src.modules.payroll.calc import D, ZERO, loan_split
from src.modules.payroll.text import check_account, check_ifsc, check_pan

_MONEY = Decimal("0.01")


def backdated_months(effective_from: date, today: date) -> int:
    """Completed months between a backdated revision and the current month."""
    if effective_from >= date(today.year, today.month, 1):
        return 0
    return (today.year - effective_from.year) * 12 + (today.month - effective_from.month)


def arrears_for_revision(*, old_monthly: Decimal, new_monthly: Decimal, months: int) -> Decimal:
    if months <= 0:
        return ZERO
    diff = D(new_monthly) - D(old_monthly)
    if diff <= 0:
        return ZERO
    return (diff * months).quantize(_MONEY)


def emi_schedule(*, balance: Decimal, emi: Decimal, annual_rate: Decimal, start: date, limit: int = 360) -> list[dict]:
    """Reducing-balance schedule. Stops if the EMI cannot reduce principal."""
    remaining = D(balance)
    rows: list[dict] = []
    year, month = start.year, start.month
    for number in range(1, limit + 1):
        if remaining <= 0:
            break
        interest, principal, remaining = loan_split(balance=remaining, emi=emi, annual_rate=annual_rate)
        rows.append(
            {
                "installment": number,
                "due_on": date(year, month, 1).isoformat(),
                "interest": str(interest),
                "principal": str(principal),
                "balance_after": str(remaining),
            }
        )
        if principal <= 0:
            rows[-1]["note"] = "EMI does not cover the interest, so the balance does not fall"
            break
        month += 1
        if month == 13:
            month = 1
            year += 1
    return rows


def validate_component_fields(
    *,
    kind: str,
    calc_type: str,
    amount: Decimal | None,
    percent: Decimal | None,
    percent_of: str | None,
    formula: str | None,
) -> None:
    if kind not in {"EARNING", "DEDUCTION"}:
        raise ValueError("kind must be EARNING or DEDUCTION")
    if calc_type not in {"FIXED", "PERCENT", "FORMULA"}:
        raise ValueError("calc_type must be FIXED, PERCENT, or FORMULA")
    if amount is not None and D(amount) < 0:
        raise ValueError("amount cannot be negative")
    if percent is not None and not (Decimal("0") <= D(percent) <= Decimal("100")):
        raise ValueError("percent must be between 0 and 100")
    if calc_type == "PERCENT":
        basis = (percent_of or "").upper()
        if basis not in {"BASIC", "DA", "HRA", "GROSS"}:
            raise ValueError("percent_of must be BASIC, DA, HRA, or GROSS")
    if calc_type == "FORMULA" and not (formula or "").strip():
        raise ValueError("formula is required")


def monthly_cash(components) -> Decimal:
    """Monthly earning total from fixed amounts and percents of basic."""
    basic = ZERO
    for component in components:
        if component.code == "BASIC" and component.calc_type == "FIXED":
            basic = D(component.amount)
    total = ZERO
    for component in components:
        if component.kind != "EARNING":
            continue
        if component.calc_type == "FIXED":
            total += D(component.amount)
        elif component.calc_type == "PERCENT" and component.percent is not None:
            basis = (component.percent_of or "BASIC").upper()
            base = basic if basis == "BASIC" else ZERO
            total += base * D(component.percent) / Decimal("100")
    return total.quantize(Decimal("0.01"))


def read_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The CSV needs a header row")
    headers = [name.strip() for name in reader.fieldnames]
    rows = []
    for raw in reader:
        cleaned = {}
        for key, value in raw.items():
            if not key:
                continue
            if isinstance(value, list):
                value = " ".join(str(item) for item in value if item)
            cleaned[key.strip()] = (value or "").strip()
        rows.append(cleaned)
    return headers, rows


def validate_employee_row(row: dict[str, str], line: int) -> list[dict]:
    errors = []
    code = row.get("employee_code") or ""
    name = row.get("full_name") or ""
    if not code:
        errors.append({"row": line, "field": "employee_code", "message": "employee_code is required"})
    elif len(code) > 64:
        errors.append({"row": line, "field": "employee_code", "message": "employee_code is too long"})
    if not name:
        errors.append({"row": line, "field": "full_name", "message": "full_name is required"})
    elif len(name) > 200:
        errors.append({"row": line, "field": "full_name", "message": "full_name is too long"})
    email = row.get("email") or ""
    if email and ("@" not in email or len(email) > 320):
        errors.append({"row": line, "field": "email", "message": "email is not valid"})
    phone = row.get("phone") or ""
    if phone and (not phone.replace("+", "").replace(" ", "").isdigit() or len(phone) > 32):
        errors.append({"row": line, "field": "phone", "message": "phone must be digits"})
    pan = row.get("pan") or ""
    if pan:
        try:
            check_pan(pan)
        except AppError as exc:
            errors.append({"row": line, "field": "pan", "message": str(exc)})
    return errors


def validate_bank_row(row: dict[str, str], line: int) -> tuple[dict | None, list[dict]]:
    errors = []
    if not (row.get("employee_code") or ""):
        errors.append({"row": line, "field": "employee_code", "message": "employee_code is required"})
    holder = row.get("account_holder") or ""
    if not holder:
        errors.append({"row": line, "field": "account_holder", "message": "account_holder is required"})
    account = ""
    ifsc = ""
    try:
        account = check_account(row.get("account_number") or "")
    except AppError as exc:
        errors.append({"row": line, "field": "account_number", "message": str(exc)})
    try:
        ifsc = check_ifsc(row.get("ifsc") or "")
    except AppError as exc:
        errors.append({"row": line, "field": "ifsc", "message": str(exc)})
    if errors:
        return None, errors
    return {
        "employee_code": row["employee_code"],
        "account_holder": holder,
        "account_number": account,
        "ifsc": ifsc,
        "bank_name": (row.get("bank_name") or "")[:120],
    }, []


def parse_bank_response(content: bytes) -> tuple[list[dict], list[dict]]:
    """Read a bank return file. status is PAID or FAILED, with a UTR when paid."""
    try:
        headers, rows = read_csv(content)
    except ValueError as exc:
        return [], [{"row": 1, "field": "file", "message": str(exc)}]
    needed = {"account_number", "status"}
    missing = needed - {header.lower() for header in headers}
    if missing:
        return [], [{"row": 1, "field": "file", "message": f"Missing columns: {', '.join(sorted(missing))}"}]
    parsed = []
    errors = []
    for index, row in enumerate(rows, start=2):
        lowered = {key.lower(): value for key, value in row.items()}
        account = "".join(ch for ch in lowered.get("account_number", "") if ch.isdigit())
        status = lowered.get("status", "").upper()
        if status in {"SUCCESS", "PAID", "PROCESSED"}:
            status = "PAID"
        elif status in {"FAILED", "REJECTED", "RETURN"}:
            status = "FAILED"
        else:
            errors.append({"row": index, "field": "status", "message": "status must be PAID or FAILED"})
            continue
        if len(account) < 9:
            errors.append({"row": index, "field": "account_number", "message": "account_number is not valid"})
            continue
        utr = lowered.get("utr") or None
        if status == "PAID" and not utr:
            errors.append({"row": index, "field": "utr", "message": "A paid row needs a UTR"})
            continue
        parsed.append(
            {
                "row": index,
                "account_number": account,
                "status": status,
                "utr": utr,
                "reason": lowered.get("reason") or None,
            }
        )
    return parsed, errors


def pf_ecr_text(rows: list[dict]) -> str:
    """EPFO ECR text. Fields are separated by #~#."""
    lines = []
    for row in rows:
        epf = D(row.get("employee_share") or 0)
        eps = D(row.get("eps") or 0)
        diff = epf - eps
        if diff < 0:
            diff = ZERO
        fields = [
            row.get("uan") or "",
            row.get("name") or "",
            str(row.get("gross") or "0.00"),
            str(row.get("epf_wages") or "0.00"),
            str(row.get("eps_wages") or "0.00"),
            str(row.get("edli_wages") or "0.00"),
            str(epf),
            str(eps),
            str(diff.quantize(_MONEY)),
            str(row.get("ncp_days") or "0"),
            "0",
        ]
        lines.append("#~#".join(fields))
    return "\n".join(lines) + ("\n" if lines else "")


def rows_csv(headers: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def _col(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def rows_xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    """A single-sheet workbook. Values are inline strings so Excel and Sheets open it."""
    sheet_rows = []
    for r_index, values in enumerate([headers, *rows], start=1):
        cells = []
        for c_index, value in enumerate(values, start=1):
            ref = f"{_col(c_index)}{r_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Payroll" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
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
        'Target="xl/workbook.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def page_rows(rows: list, *, q: str | None, limit: int | None, offset: int, text_of) -> tuple[list, int]:
    if q:
        needle = q.strip().lower()
        if needle:
            rows = [row for row in rows if needle in text_of(row).lower()]
    total = len(rows)
    if limit is None:
        return rows[offset:], total
    return rows[offset : offset + limit], total
