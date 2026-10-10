from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

AttendanceStatus = Literal["PRESENT", "ABSENT", "HALF_DAY", "LEAVE"]


class _Notes(BaseModel):
    @field_validator("notes", mode="before", check_fields=False)
    @classmethod
    def _trim_notes(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class AttendanceMark(_Notes):
    employee_id: UUID
    attendance_date: date
    status: AttendanceStatus
    notes: str | None = Field(default=None, max_length=500)


class AttendanceUpdate(_Notes):
    status: AttendanceStatus | None = None
    notes: str | None = Field(default=None, max_length=500)


class AttendanceBulkEntry(_Notes):
    employee_id: UUID
    status: AttendanceStatus
    notes: str | None = Field(default=None, max_length=500)


class AttendanceBulk(BaseModel):
    attendance_date: date
    entries: list[AttendanceBulkEntry] = Field(min_length=1, max_length=1000)

    @field_validator("entries")
    @classmethod
    def _unique_employees(
        cls, v: list[AttendanceBulkEntry]
    ) -> list[AttendanceBulkEntry]:
        ids = [e.employee_id for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Each employee can appear only once per day")
        return v


class AttendanceOut(BaseModel):
    id: UUID
    school_id: UUID
    employee_id: UUID
    employee_name: str
    employee_code: str | None = None
    attendance_date: date
    status: str
    notes: str | None = None
    marked_by: UUID | None = None
    marked_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


class AttendanceCounts(BaseModel):
    present: int = 0
    absent: int = 0
    half_day: int = 0
    leave: int = 0


class DailySummary(AttendanceCounts):
    unmarked: int = 0
    total: int = 0


class DailyRow(BaseModel):
    employee_id: UUID
    full_name: str
    employee_code: str | None = None
    staff_type: str
    department_name: str | None = None
    designation_name: str | None = None
    employee_active: bool
    record: AttendanceOut | None = None


class DailySheet(BaseModel):
    attendance_date: date
    summary: DailySummary
    rows: list[DailyRow]


class BulkResult(BaseModel):
    created: int
    updated: int
    records: list[AttendanceOut]


class EmployeeAttendanceSummary(AttendanceCounts):
    employee_id: UUID
    full_name: str
    employee_code: str | None = None
    department_name: str | None = None
    marked_days: int = 0
    attendance_rate: float | None = None


class RangeSummary(BaseModel):
    date_from: date
    date_to: date
    totals: AttendanceCounts
    employees: list[EmployeeAttendanceSummary]
