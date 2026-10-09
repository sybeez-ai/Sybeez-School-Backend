from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class TeacherCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    employee_code: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date | None = None
    joining_date: date | None = None
    qualification: str | None = Field(default=None, max_length=255)
    photo_url: str | None = None
    status: str | None = Field(default="ACTIVE", max_length=32)
    # Optional first teaching assignment
    class_id: UUID | None = None
    subject_id: UUID | None = None
    academic_year_id: UUID | None = None


class TeacherUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    employee_code: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date | None = None
    joining_date: date | None = None
    qualification: str | None = Field(default=None, max_length=255)
    photo_url: str | None = None
    status: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None


class TeacherStatusUpdate(BaseModel):
    status: str | None = None
    is_active: bool | None = None


class TeacherAssignmentBrief(BaseModel):
    id: UUID
    subject_id: UUID
    subject_name: str | None = None
    class_id: UUID
    class_name: str | None = None
    section: str | None = None
    grade_id: UUID | None = None
    grade_name: str | None = None
    academic_year_id: UUID | None = None
    academic_year_name: str | None = None
    student_count: int = 0
    is_class_teacher: bool = False


class ClassTeacherSectionBrief(BaseModel):
    class_id: UUID
    class_name: str | None = None
    section: str | None = None
    grade_name: str | None = None
    academic_year_name: str | None = None
    student_count: int = 0


class TeacherOut(BaseModel):
    id: UUID
    school_id: UUID
    user_id: UUID
    teacher_code: str | None = None
    employee_code: str | None = None
    title: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None
    joining_date: date | None = None
    qualification: str | None = None
    photo_url: str | None = None
    status: str = "ACTIVE"
    is_active: bool = True
    created_at: datetime
    assignments: list[TeacherAssignmentBrief] = []
    class_teacher_of: list[ClassTeacherSectionBrief] = []
    temporary_password: str | None = None

    model_config = {"from_attributes": True}


class TeacherDashboardOut(BaseModel):
    teacher: TeacherOut
    classes: list[TeacherAssignmentBrief]
    subjects: list[str]
    total_students: int
    class_teacher_sections: list[ClassTeacherSectionBrief]
