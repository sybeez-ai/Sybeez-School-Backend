"""Match a WhatsApp number to profile phones. No extra link is required when they match."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass

_DIGITS = re.compile(r"\D+")


def to_e164(phone: str | None) -> str:
    """Digits only, with a country code.

    A 10-digit number is treated as India (+91), which is how profile phones are
    saved. A leading 0 on an 11-digit Indian mobile is dropped. Numbers that
    already include a country code are left as that country code. `00` is the
    international prefix and is removed.
    """
    digits = _DIGITS.sub("", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def same_phone(left: str | None, right: str | None) -> bool:
    a, b = to_e164(left), to_e164(right)
    return bool(a) and a == b


@dataclass(frozen=True)
class PhoneHit:
    """One saved phone the service already loaded. school_id and role are filled in by the caller."""

    phone: str
    user_id: uuid.UUID
    school_id: uuid.UUID | None
    role: str | None
    label: str
    student_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ProfileChoice:
    user_id: uuid.UUID
    school_id: uuid.UUID | None
    role: str
    label: str
    student_id: uuid.UUID | None = None


def matching_profiles(incoming: str, hits: list[PhoneHit]) -> list[ProfileChoice]:
    """Keep hits whose saved phone is the same E.164 number. One row per user, school, and role."""
    target = to_e164(incoming)
    if not target:
        return []
    chosen: dict[tuple, ProfileChoice] = {}
    for hit in hits:
        if to_e164(hit.phone) != target or not hit.role:
            continue
        key = (hit.user_id, hit.school_id, hit.role)
        label = hit.label.strip() or hit.role
        current = chosen.get(key)
        if current is None or len(label) > len(current.label):
            chosen[key] = ProfileChoice(
                user_id=hit.user_id,
                school_id=hit.school_id,
                role=hit.role,
                label=label,
                student_id=hit.student_id,
            )
    return list(chosen.values())


def new_link_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_link_code(code: str, user_id: uuid.UUID, secret: str) -> str:
    """Keyed hash so a copy of the table is not a list of 6-digit codes."""
    return hmac.new(secret.encode("utf-8"), f"{user_id}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def link_code_matches(code: str, user_id: uuid.UUID, secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_link_code(code, user_id, secret), stored_hash)
