"""Hide bank account numbers and PAN before they reach the model, the browser, or the audit log."""

from __future__ import annotations

import re

_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)
_ACCOUNT = re.compile(r"\b\d{9,18}\b")
_SENSITIVE_KEYS = {
    "pan",
    "account_number",
    "account",
    "bank_account",
    "beneficiary_account_number",
    "bene_acc_no",
}


def mask_pan(value: str) -> str:
    text = (value or "").strip().upper()
    if len(text) <= 4:
        return "****"
    return ("*" * (len(text) - 4)) + text[-4:]


def mask_account(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) <= 4:
        return "****"
    return ("*" * (len(digits) - 4)) + digits[-4:]


def redact_text(value: str) -> str:
    text = _PAN.sub(lambda match: mask_pan(match.group(0)), value or "")
    return _ACCOUNT.sub(lambda match: mask_account(match.group(0)), text)


def mask_payload(value):
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            name = str(key).lower()
            if name in _SENSITIVE_KEYS and isinstance(item, str):
                masked[key] = mask_pan(item) if name == "pan" else mask_account(item)
            else:
                masked[key] = mask_payload(item)
        return masked
    if isinstance(value, list):
        return [mask_payload(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
