from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

RequestType = Literal[
    "PROFILE_UPDATE", "DOCUMENT_REQUEST", "LETTER_REQUEST", "HR_QUERY", "OTHER"
]
RequestStatus = Literal[
    "PENDING", "IN_REVIEW", "APPROVED", "REJECTED", "RESOLVED", "CANCELLED"
]
DecisionStatus = Literal["IN_REVIEW", "APPROVED", "REJECTED", "RESOLVED"]

PROFILE_FIELDS = (
    "phone",
    "address",
    "emergency_contact_name",
    "emergency_contact_phone",
    "qualification",
)


def _strip(v):
    return v.strip() if isinstance(v, str) else v


class RequestCreate(BaseModel):
    request_type: RequestType
    subject: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=4000)
    requested_changes: dict[str, str] | None = None

    _trim = field_validator("subject", "details", mode="before")(_strip)

    @field_validator("requested_changes")
    @classmethod
    def _changes(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if not v:
            return None
        unknown = set(v) - set(PROFILE_FIELDS)
        if unknown:
            raise ValueError(
                f"Unsupported profile fields: {', '.join(sorted(unknown))}"
            )
        clean = {k: s.strip() for k, s in v.items() if isinstance(s, str) and s.strip()}
        if any(len(s) > 255 for s in clean.values()):
            raise ValueError("Profile values must be 255 characters or fewer")
        return clean or None

    @model_validator(mode="after")
    def _profile_only(self):
        if self.requested_changes and self.request_type != "PROFILE_UPDATE":
            raise ValueError(
                "Profile changes can only be attached to a profile update request"
            )
        if self.request_type == "PROFILE_UPDATE" and not (
            self.requested_changes or self.details
        ):
            raise ValueError("Describe the profile change you need")
        return self


class AdminRequestCreate(RequestCreate):
    employee_id: UUID


class RequestDecision(BaseModel):
    status: DecisionStatus
    note: str | None = Field(default=None, max_length=2000)

    _trim = field_validator("note", mode="before")(_strip)

    @model_validator(mode="after")
    def _reason(self):
        if self.status == "REJECTED" and not self.note:
            raise ValueError("Give a reason when rejecting a request")
        return self


class RequestComment(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    _trim = field_validator("message", mode="before")(_strip)


class RequestEventOut(BaseModel):
    id: UUID
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    message: str | None = None
    actor_name: str | None = None
    actor_is_employee: bool = False
    created_at: datetime


class RequestOut(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: str
    employee_code: str | None = None
    department_name: str | None = None
    request_type: RequestType
    subject: str
    details: str
    requested_changes: dict[str, str] | None = None
    current_values: dict[str, str | None] | None = None
    status: RequestStatus
    source: Literal["EMPLOYEE", "ADMIN"]
    submitted_by_name: str | None = None
    decision_note: str | None = None
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    events: list[RequestEventOut] | None = None


class MyProfileOut(BaseModel):
    id: UUID
    full_name: str
    employee_code: str | None = None
    staff_type: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None
    joining_date: date | None = None
    qualification: str | None = None
    status: str
    is_active: bool
    department_name: str | None = None
    designation_name: str | None = None
    employment_type: str | None = None
    address: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
