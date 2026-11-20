from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

DocumentCategory = Literal[
    "ID_PROOF", "CONTRACT", "QUALIFICATION", "MEDICAL", "BANK", "TAX", "DISCIPLINARY", "OTHER"
]
CATEGORY_PATTERN = "^(ID_PROOF|CONTRACT|QUALIFICATION|MEDICAL|BANK|TAX|DISCIPLINARY|OTHER)$"
DocumentStatus = Literal["ACTIVE", "ARCHIVED"]
VerificationStatus = Literal["PENDING", "VERIFIED", "REJECTED"]


class EmployeeDocumentUpdate(BaseModel):
    category: DocumentCategory | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    shared_with_employee: bool | None = None
    # Nullable: sending null clears the expiry date.
    expiry_date: date | None = None

    @field_validator("title", "notes", mode="before")
    @classmethod
    def _trim(cls, v):
        return v.strip() if isinstance(v, str) else v


class DocumentVerify(BaseModel):
    status: Literal["VERIFIED", "REJECTED"]
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note", mode="before")
    @classmethod
    def _trim(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v


class DownloadLink(BaseModel):
    url: str
    expires_in: int
    storage: Literal["local", "s3"]


class EmployeeDocumentOut(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: str
    employee_code: str | None = None
    department_name: str | None = None
    category: DocumentCategory
    is_sensitive: bool
    title: str
    notes: str | None = None
    status: DocumentStatus
    shared_with_employee: bool
    file_name: str
    mime_type: str | None = None
    file_size: int
    verification_status: VerificationStatus
    verified_by_name: str | None = None
    verified_at: datetime | None = None
    verification_note: str | None = None
    expiry_date: date | None = None
    is_expired: bool = False
    expires_soon: bool = False
    uploaded_by_name: str | None = None
    updated_by_name: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChecklistRow(BaseModel):
    employee_id: UUID
    employee_name: str
    employee_code: str | None = None
    department_name: str | None = None
    staff_type: str
    document_count: int
    present: list[DocumentCategory]
    missing: list[DocumentCategory]
    expired: list[DocumentCategory] = []


class DocumentChecklist(BaseModel):
    required: list[DocumentCategory]
    complete: int
    incomplete: int
    rows: list[ChecklistRow]
