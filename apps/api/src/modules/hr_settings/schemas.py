from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentCategory = Literal[
    "ID_PROOF", "CONTRACT", "QUALIFICATION", "MEDICAL", "BANK", "OTHER"
]


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
    school_timezone: str
    is_default: bool
    updated_at: datetime | None = None
    updated_by_name: str | None = None


class HrSettingsUpdate(BaseModel):
    """Partial update; every field that is sent must have a value."""

    model_config = ConfigDict(extra="forbid")

    letter_reference_prefix: str | None = Field(
        default=None, min_length=1, max_length=10
    )
    required_document_categories: list[DocumentCategory] | None = Field(
        default=None, max_length=6
    )
    leave_max_days: int | None = Field(default=None, ge=1, le=366)
    onboarding_target_days: int | None = Field(default=None, ge=0, le=365)
    notify_leave: bool | None = None
    notify_requests: bool | None = None
    notify_letters: bool | None = None
    notify_documents: bool | None = None
    notify_onboarding: bool | None = None

    @field_validator("letter_reference_prefix", mode="before")
    @classmethod
    def _prefix(cls, v):
        if v is None:
            return v
        v = str(v).strip().upper()
        if not v.isascii() or not v.isalnum():
            raise ValueError("Use 1–10 letters or digits for the letter prefix")
        return v

    @field_validator("required_document_categories")
    @classmethod
    def _unique(cls, v):
        return list(dict.fromkeys(v)) if v is not None else v
