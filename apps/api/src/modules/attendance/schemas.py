from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.shared.enums import AttendanceStatus


class AttendanceCreate(BaseModel):
    student_id: UUID
    class_id: UUID
    attended_on: date
    status: AttendanceStatus = AttendanceStatus.PRESENT


class AttendanceBulkCreate(BaseModel):
    records: list[AttendanceCreate] = Field(min_length=1, max_length=200)


class AttendanceUpdate(BaseModel):
    status: AttendanceStatus


class AttendanceOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    class_id: UUID
    attended_on: date
    status: str
    marked_by: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}
