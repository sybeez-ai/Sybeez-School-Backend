from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    grade_id: UUID
    academic_year_id: UUID
    section: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=200)
    class_teacher_id: UUID | None = None


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    section: str | None = Field(default=None, min_length=1, max_length=16)
    capacity: int | None = Field(default=None, ge=1, le=200)
    # Pass null explicitly to clear class teacher
    class_teacher_id: UUID | None = None


class ClassOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    grade_id: UUID
    academic_year_id: UUID
    section: str | None
    capacity: int | None = None
    class_teacher_id: UUID | None = None
    class_teacher_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
