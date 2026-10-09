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
    academic_year_name: str | None = None
    created_at: datetime
    teacher_name: str | None = None
    subject_name: str | None = None
    class_name: str | None = None
    section: str | None = None
    grade_name: str | None = None
    student_count: int = 0
    is_class_teacher: bool = False

    model_config = {"from_attributes": True}
