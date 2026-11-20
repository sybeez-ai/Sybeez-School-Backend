from __future__ import annotations

from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentCategory = Literal[
    "ID_PROOF", "CONTRACT", "QUALIFICATION", "MEDICAL", "BANK", "TAX", "DISCIPLINARY", "OTHER"
]


def _clean_checklist(v: list[str] | None) -> list[str] | None:
    if v is None:
        return v
    items = [str(x).strip() for x in v if str(x).strip()]
    if not items:
        raise ValueError("A checklist needs at least one task")
    if any(len(x) > 200 for x in items):
        raise ValueError("Checklist tasks must be 200 characters or fewer")
    return list(dict.fromkeys(items))


class HrSettingsOut(BaseModel):
    letter_reference_prefix: str
    required_document_categories: list[DocumentCategory]
    leave_max_days: int
    onboarding_target_days: int
    notify_leave: bool
    notify_requests: bool
    notify_letters: bool
    notify_documents: bool
    notify_onboarding: bool
    employee_code_prefix: str | None
    working_weekdays: list[int]
    work_start_time: time | None
    late_grace_minutes: int
    leave_balance_enforced: bool
    leave_min_notice_days: int
    document_expiry_warning_days: int
    onboarding_checklist: list[str]
    offboarding_checklist: list[str]
    school_timezone: str
    is_default: bool
    updated_at: datetime | None = None
    updated_by_name: str | None = None


class HrSettingsUpdate(BaseModel):
    """Partial update; every field that is sent must have a value (except the nullable ones below)."""

    model_config = ConfigDict(extra="forbid")

    letter_reference_prefix: str | None = Field(
        default=None, min_length=1, max_length=10
    )
    required_document_categories: list[DocumentCategory] | None = Field(
        default=None, max_length=8
    )
    leave_max_days: int | None = Field(default=None, ge=1, le=366)
    onboarding_target_days: int | None = Field(default=None, ge=0, le=365)
    notify_leave: bool | None = None
    notify_requests: bool | None = None
    notify_letters: bool | None = None
    notify_documents: bool | None = None
    notify_onboarding: bool | None = None
    # Nullable: sending null clears the value.
    employee_code_prefix: str | None = Field(default=None, max_length=10)
    work_start_time: time | None = None
    working_weekdays: list[int] | None = Field(default=None, min_length=1, max_length=7)
    late_grace_minutes: int | None = Field(default=None, ge=0, le=240)
    leave_balance_enforced: bool | None = None
    leave_min_notice_days: int | None = Field(default=None, ge=0, le=90)
    document_expiry_warning_days: int | None = Field(default=None, ge=0, le=365)
    onboarding_checklist: list[str] | None = Field(default=None, max_length=30)
    offboarding_checklist: list[str] | None = Field(default=None, max_length=30)

    @field_validator("letter_reference_prefix", mode="before")
    @classmethod
    def _prefix(cls, v):
        if v is None:
            return v
        v = str(v).strip().upper()
        if not v.isascii() or not v.isalnum():
            raise ValueError("Use 1–10 letters or digits for the letter prefix")
        return v

    @field_validator("employee_code_prefix", mode="before")
    @classmethod
    def _code_prefix(cls, v):
        if v is None:
            return v
        v = str(v).strip().upper()
        if not v:
            return None
        if not v.isascii() or not v.isalnum():
            raise ValueError("Use 1–10 letters or digits for the employee ID prefix")
        return v

    @field_validator("required_document_categories")
    @classmethod
    def _unique(cls, v):
        return list(dict.fromkeys(v)) if v is not None else v

    @field_validator("working_weekdays")
    @classmethod
    def _weekdays(cls, v):
        if v is None:
            return v
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Working days must be weekday numbers 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @field_validator("onboarding_checklist", "offboarding_checklist")
    @classmethod
    def _checklist(cls, v):
        return _clean_checklist(v)
