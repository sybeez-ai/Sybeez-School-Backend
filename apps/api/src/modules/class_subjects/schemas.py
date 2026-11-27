from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ClassSubjectCreate(BaseModel):
    subject_id: UUID
    teacher_id: UUID | None = None


class ClassSubjectUpdate(BaseModel):
    teacher_id: UUID | None = None


class ClassSubjectOut(BaseModel):
    id: UUID
    school_id: UUID
    class_id: UUID
    class_name: str
    subject_id: UUID
    subject_name: str
    subject_code: str
    teacher_id: UUID | None
    teacher_name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
