"""Shared filter helpers for reports and analytics."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class CommonFilters(BaseModel):
    academic_year_id: UUID | None = None
    grade_id: UUID | None = None
    section_id: UUID | None = None
    subject_id: UUID | None = None
    exam_id: UUID | None = None
    from_date: date | None = None
    to_date: date | None = None
    student_status: str | None = None
