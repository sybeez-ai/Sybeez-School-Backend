from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EnrollmentCreate(BaseModel):
    student_id: UUID
    class_id: UUID
    academic_year_id: UUID
    grade_id: UUID | None = None
    roll_number: str | None = Field(default=None, max_length=32)


class EnrollmentOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    class_id: UUID
    academic_year_id: UUID
    grade_id: UUID | None = None
    roll_number: str | None = None
    status: str
    enrolled_at: datetime | None = None
    left_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    class_name: str | None = None
    section: str | None = None
    grade_name: str | None = None
    academic_year_name: str | None = None
    student_name: str | None = None

    model_config = {"from_attributes": True}
