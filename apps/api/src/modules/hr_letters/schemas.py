from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

LetterType = Literal["OFFER", "APPOINTMENT", "EXPERIENCE", "RELIEVING", "OTHER"]
LetterStatus = Literal["DRAFT", "ISSUED", "REVOKED"]


def _strip(v):
    return v.strip() if isinstance(v, str) else v


class LetterCreate(BaseModel):
    employee_id: UUID
    letter_type: LetterType
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=20000)

    _trim = field_validator("title", "body", mode="before")(_strip)


class LetterUpdate(BaseModel):
    letter_type: LetterType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=20000)

    _trim = field_validator("title", "body", mode="before")(_strip)


class LetterIssue(BaseModel):
    issue_date: date | None = None


class LetterRevoke(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    _trim = field_validator("reason", mode="before")(_strip)


class LetterTemplateOut(BaseModel):
    letter_type: LetterType
    title: str
    body: str


class LetterOut(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: str
    employee_code: str | None = None
    department_name: str | None = None
    designation_name: str | None = None
    letter_type: LetterType
    title: str
    body: str
    status: LetterStatus
    reference_number: str | None = None
    issue_date: date | None = None
    issued_at: datetime | None = None
    issued_by_name: str | None = None
    revoked_at: datetime | None = None
    revoked_by_name: str | None = None
    revoke_reason: str | None = None
    created_by_name: str | None = None
    updated_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
