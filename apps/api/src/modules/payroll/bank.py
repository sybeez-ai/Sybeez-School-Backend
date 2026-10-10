"""Bank transfer files. GENERIC_NEFT is the built-in format. Register more with register_bank_format."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from decimal import Decimal

Formatter = Callable[[list[dict]], str]
_FORMATS: dict[str, Formatter] = {}


def register_bank_format(code: str, formatter: Formatter) -> None:
    _FORMATS[code.strip().upper()] = formatter


def _csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "beneficiary_name",
            "account_number",
            "ifsc",
            "amount",
            "narration",
            "employee_code",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "beneficiary_name": row.get("beneficiary_name") or "",
                "account_number": row.get("account_number") or "",
                "ifsc": row.get("ifsc") or "",
                "amount": str(row.get("amount") or Decimal("0.00")),
                "narration": row.get("narration") or "",
                "employee_code": row.get("employee_code") or "",
            }
        )
    return buffer.getvalue()


register_bank_format("GENERIC_NEFT", _csv)


def render_bank_file(code: str, rows: list[dict]) -> tuple[str, str]:
    key = (code or "GENERIC_NEFT").upper()
    formatter = _FORMATS.get(key)
    if formatter is None:
        raise ValueError(f"Unknown bank format {key}")
    return formatter(rows), "text/csv" if key == "GENERIC_NEFT" else "text/plain"
