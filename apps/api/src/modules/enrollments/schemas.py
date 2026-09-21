from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EnrollmentCreate(BaseModel):
    student_id: UUID
    class_id: UUID
    academic_year_id: UUID
    grade_id: UUID | None = None


class EnrollmentOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    class_id: UUID
    academic_year_id: UUID
    grade_id: UUID | None = None
    status: str
    enrolled_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
