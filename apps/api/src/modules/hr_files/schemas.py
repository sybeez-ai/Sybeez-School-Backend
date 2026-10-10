from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

DocumentCategory = Literal[
    "ID_PROOF", "CONTRACT", "QUALIFICATION", "MEDICAL", "BANK", "OTHER"
]
DocumentStatus = Literal["ACTIVE", "ARCHIVED"]


class EmployeeDocumentUpdate(BaseModel):
    category: DocumentCategory | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    shared_with_employee: bool | None = None

    @field_validator("title", "notes", mode="before")
    @classmethod
    def _trim(cls, v):
        return v.strip() if isinstance(v, str) else v


class EmployeeDocumentOut(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: str
    employee_code: str | None = None
    department_name: str | None = None
    category: DocumentCategory
    title: str
    notes: str | None = None
    status: DocumentStatus
    shared_with_employee: bool
    file_name: str
    mime_type: str | None = None
    file_size: int
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


class DocumentChecklist(BaseModel):
    required: list[DocumentCategory]
    complete: int
    incomplete: int
    rows: list[ChecklistRow]
