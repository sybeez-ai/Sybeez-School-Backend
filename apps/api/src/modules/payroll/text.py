"""Strip control characters from payroll text. Validators reject secrets and odd account shapes."""

from __future__ import annotations

import re

from src.core.exceptions import AppError

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
ACCOUNT = re.compile(r"^[0-9]{9,18}$")


def clean(value: str | None, *, max_len: int, field: str = "value") -> str | None:
    if value is None:
        return None
    text = _CONTROL.sub("", str(value)).strip()
    if not text:
        raise AppError(f"{field} is required", status_code=422)
    if len(text) > max_len:
        raise AppError(f"{field} must be at most {max_len} characters", status_code=422)
    return text


def require_text(value: str, *, max_len: int, field: str) -> str:
    text = clean(value, max_len=max_len, field=field)
    if not text:
        raise AppError(f"{field} is required", status_code=422)
    return text


def check_pan(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    pan = require_text(value, max_len=10, field="PAN").upper()
    if not PAN.match(pan):
        raise AppError("PAN must look like ABCDE1234F", status_code=422)
    return pan


def check_ifsc(value: str) -> str:
    ifsc = require_text(value, max_len=11, field="IFSC").upper()
    if not IFSC.match(ifsc):
        raise AppError("IFSC must be 11 characters, with 0 as the fifth", status_code=422)
    return ifsc


def check_account(value: str) -> str:
    account = require_text(value, max_len=18, field="Account number")
    if not ACCOUNT.match(account):
        raise AppError("Account number must be 9 to 18 digits", status_code=422)
    return account
