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


def _table(headers: list[str], rows: list[dict], mapping: dict[str, str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({header: row.get(mapping[header], "") for header in headers})
    return buffer.getvalue()


def _hdfc(rows: list[dict]) -> str:
    headers = [
        "Transaction Type",
        "Beneficiary Account Number",
        "Instrument Amount",
        "Beneficiary Name",
        "IFSC Code",
        "Customer Reference Number",
        "Payment details 1",
    ]
    mapped = []
    for index, row in enumerate(rows, start=1):
        mapped.append(
            {
                "Transaction Type": "N",
                "Beneficiary Account Number": row.get("account_number") or "",
                "Instrument Amount": str(row.get("amount") or Decimal("0.00")),
                "Beneficiary Name": row.get("beneficiary_name") or "",
                "IFSC Code": row.get("ifsc") or "",
                "Customer Reference Number": row.get("employee_code") or str(index),
                "Payment details 1": row.get("narration") or "",
            }
        )
    return _table(headers, mapped, {header: header for header in headers})


def _icici(rows: list[dict]) -> str:
    headers = ["PYMT_MODE", "BNF_NAME", "BENE_ACC_NO", "BENE_IFSC", "AMOUNT", "CREDIT_NARR", "DEBIT_NARR"]
    mapped = [
        {
            "PYMT_MODE": "NEFT",
            "BNF_NAME": row.get("beneficiary_name") or "",
            "BENE_ACC_NO": row.get("account_number") or "",
            "BENE_IFSC": row.get("ifsc") or "",
            "AMOUNT": str(row.get("amount") or Decimal("0.00")),
            "CREDIT_NARR": row.get("narration") or "",
            "DEBIT_NARR": row.get("employee_code") or "",
        }
        for row in rows
    ]
    return _table(headers, mapped, {header: header for header in headers})


def _sbi(rows: list[dict]) -> str:
    headers = ["BENEFICIARY NAME", "ACCOUNT NUMBER", "IFSC", "AMOUNT", "NARRATION"]
    mapped = [
        {
            "BENEFICIARY NAME": row.get("beneficiary_name") or "",
            "ACCOUNT NUMBER": row.get("account_number") or "",
            "IFSC": row.get("ifsc") or "",
            "AMOUNT": str(row.get("amount") or Decimal("0.00")),
            "NARRATION": row.get("narration") or "",
        }
        for row in rows
    ]
    return _table(headers, mapped, {header: header for header in headers})


def _axis(rows: list[dict]) -> str:
    headers = [
        "Payment Method",
        "Beneficiary Name",
        "Beneficiary Account Number",
        "Beneficiary IFSC",
        "Amount",
        "Customer Reference",
        "Narration",
    ]
    mapped = [
        {
            "Payment Method": "NEFT",
            "Beneficiary Name": row.get("beneficiary_name") or "",
            "Beneficiary Account Number": row.get("account_number") or "",
            "Beneficiary IFSC": row.get("ifsc") or "",
            "Amount": str(row.get("amount") or Decimal("0.00")),
            "Customer Reference": row.get("employee_code") or "",
            "Narration": row.get("narration") or "",
        }
        for row in rows
    ]
    return _table(headers, mapped, {header: header for header in headers})


register_bank_format("GENERIC_NEFT", _csv)
register_bank_format("HDFC", _hdfc)
register_bank_format("ICICI", _icici)
register_bank_format("SBI", _sbi)
register_bank_format("AXIS", _axis)

BANK_FORMATS = ("GENERIC_NEFT", "HDFC", "ICICI", "SBI", "AXIS")


def render_bank_file(code: str, rows: list[dict]) -> tuple[str, str]:
    key = (code or "GENERIC_NEFT").upper()
    formatter = _FORMATS.get(key)
    if formatter is None:
        raise ValueError(f"Unknown bank format {key}")
    return formatter(rows), "text/csv"
