from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ── enum literals ─────────────────────────────────────────────────────────────
DocumentCategory = Literal[
    "STUDY_MATERIAL", "CIRCULAR", "CERTIFICATE", "REPORT_CARD", "POLICY", "OTHER"
]
DocumentVisibility = Literal["STUDENTS", "TEACHERS", "PARENTS", "SCHOOL", "ALL"]


class DocumentCreate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=255)]
    category: DocumentCategory = "STUDY_MATERIAL"
    description: str | None = None
    visibility: DocumentVisibility = "STUDENTS"
    class_id: UUID | None = None
    subject_id: UUID | None = None
    # Optional: pre-existing URL (e.g. cloud storage).  Use upload endpoint for file bytes.
    file_url: str | None = None


class DocumentUpdate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    category: DocumentCategory | None = None
    description: str | None = None
    visibility: DocumentVisibility | None = None
    class_id: UUID | None = None
    subject_id: UUID | None = None


class DocumentOut(BaseModel):
    id: UUID
    school_id: UUID
    uploaded_by: UUID | None
    title: str
    category: str
    description: str | None
    visibility: str
    is_active: bool
    file_url: str | None
    file_key: str | None
    file_name: str | None
    file_size: int | None
    mime_type: str | None
    class_id: UUID | None
    subject_id: UUID | None
    created_at: datetime
    updated_at: datetime
    # Resolved download URL (preferred over file_url for clients)
    url: str | None = None

    model_config = {"from_attributes": True}
