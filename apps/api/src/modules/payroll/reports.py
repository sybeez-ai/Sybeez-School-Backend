"""Downloadable payroll reports. Values are escaped so a spreadsheet does not treat them as formulas."""

from __future__ import annotations

import csv
import io


def cell(value) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def csv_file(headers: list[str], rows: list[list]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([cell(item) for item in row])
    return buffer.getvalue()


def professional_tax_csv(rows: list[dict]) -> str:
    return csv_file(
        ["employee_id", "employee_name", "employee_code", "state", "gender", "pt_amount"],
        [
            [row.get("employee_id"), row.get("employee_name"), row.get("employee_code"), row.get("state"), row.get("gender"), row.get("pt_amount")]
            for row in rows
        ],
    )


def tds_24q_csv(rows: list[dict]) -> str:
    """Salary TDS annexure for one run. Section 192 is the salary section of Form 24Q."""
    return csv_file(
        ["employee_id", "employee_name", "pan", "section", "gross_amount", "tds_amount", "tax_regime"],
        [
            [
                row.get("employee_id"),
                row.get("employee_name"),
                row.get("pan"),
                "192",
                row.get("gross_amount"),
                row.get("tds_amount"),
                row.get("tax_regime"),
            ]
            for row in rows
        ],
    )


def distinct_branches(names: list[str | None]) -> list[str]:
    seen: set[str] = set()
    chosen: list[str] = []
    for raw in names:
        name = (raw or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        chosen.append(name)
    chosen.sort(key=str.lower)
    return chosen
