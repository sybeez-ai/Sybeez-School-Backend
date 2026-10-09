from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


REPORT_CATALOG = [
    {"id": "student_list", "category": "Academic", "name": "Student List"},
    {"id": "enrollment", "category": "Academic", "name": "Enrollment Report"},
    {"id": "class_report", "category": "Academic", "name": "Class Report"},
    {"id": "attendance", "category": "Attendance", "name": "Attendance Report"},
    {"id": "absence", "category": "Attendance", "name": "Absence Report"},
    {"id": "late", "category": "Attendance", "name": "Late Report"},
    {"id": "homework_submission", "category": "Homework", "name": "Submission Report"},
    {"id": "homework_missing", "category": "Homework", "name": "Missing Homework"},
    {"id": "exam_marks", "category": "Exams", "name": "Marks Report"},
    {"id": "exam_results", "category": "Exams", "name": "Result Report"},
    {"id": "fee_collection", "category": "Fees", "name": "Collection Report"},
    {"id": "fee_outstanding", "category": "Fees", "name": "Outstanding Report"},
    {"id": "fee_overdue", "category": "Fees", "name": "Overdue Report"},
]


class ReportGenerateIn(BaseModel):
    report_type: str
    academic_year_id: UUID | None = None
    grade_id: UUID | None = None
    section_id: UUID | None = None
    subject_id: UUID | None = None
    exam_id: UUID | None = None
    from_date: date | None = None
    to_date: date | None = None
    fields: list[str] = Field(default_factory=list)


class ReportResultOut(BaseModel):
    report_type: str
    title: str
    columns: list[str]
    rows: list[dict]
    total_rows: int


class SavedReportCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    report_type: str
    configuration: dict = Field(default_factory=dict)


class SavedReportUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    configuration: dict | None = None


class SavedReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    report_type: str
    configuration_json: str
    created_by: UUID | None = None
    created_at: object
