"""Phone normalization and log masking. Never log a raw number."""

from __future__ import annotations

import re

_OPT_OUT = frozenset(
    {
        "STOP",
        "STOPALL",
        "UNSUBSCRIBE",
        "CANCEL",
        "END",
        "QUIT",
        "OPTOUT",
        "OPT OUT",
        "OPT-OUT",
    }
)


def normalize_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "91" + digits
    return digits


def phone_variants(phone: str | None) -> list[str]:
    digits = normalize_phone(phone)
    if not digits:
        return []
    last10 = digits[-10:]
    return list({digits, f"+{digits}", last10, f"91{last10}", f"+91{last10}"})


def mask_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) <= 4:
        return "****"
    return ("*" * (len(digits) - 4)) + digits[-4:]


def mask_text(value: str | None) -> str:
    return re.sub(r"\d{6,}", lambda match: mask_phone(match.group(0)), value or "")


def is_opt_out_keyword(body: str | None) -> bool:
    compact = " ".join((body or "").strip().upper().split())
    return compact in _OPT_OUT
