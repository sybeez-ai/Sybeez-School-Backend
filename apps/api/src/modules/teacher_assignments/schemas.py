from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TeacherAssignmentCreate(BaseModel):
    teacher_id: UUID
    subject_id: UUID
    class_id: UUID
    academic_year_id: UUID | None = None


class TeacherAssignmentOut(BaseModel):
    id: UUID
    school_id: UUID
    teacher_id: UUID
    subject_id: UUID
    class_id: UUID
    academic_year_id: UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
