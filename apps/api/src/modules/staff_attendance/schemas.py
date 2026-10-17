from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

AttendanceStatus = Literal["PRESENT", "ABSENT", "HALF_DAY", "LEAVE"]
TIMED_STATUSES = ("PRESENT", "HALF_DAY")


class _Notes(BaseModel):
    @field_validator("notes", "reason", mode="before", check_fields=False)
    @classmethod
    def _trim_notes(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class _Times(BaseModel):
    check_in: time | None = None
    check_out: time | None = None

    @model_validator(mode="after")
    def _times_ordered(self):
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("Check-out must be after check-in")
        status = getattr(self, "status", None)
        if status and status not in TIMED_STATUSES and (self.check_in or self.check_out):
            raise ValueError("Check-in/out times apply only to Present or Half day")
        return self


class AttendanceMark(_Notes, _Times):
    employee_id: UUID
    attendance_date: date
    status: AttendanceStatus
    notes: str | None = Field(default=None, max_length=500)


class AttendanceUpdate(_Notes, _Times):
    status: AttendanceStatus | None = None
    notes: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500)


class AttendanceBulkEntry(_Notes, _Times):
    employee_id: UUID
    status: AttendanceStatus
    notes: str | None = Field(default=None, max_length=500)


class AttendanceBulk(BaseModel):
    attendance_date: date
    entries: list[AttendanceBulkEntry] = Field(min_length=1, max_length=1000)
    # Recorded in the audit trail of every saved mark this sheet changes.
    correction_reason: str | None = Field(default=None, max_length=500)

    @field_validator("correction_reason", mode="before")
    @classmethod
    def _trim_reason(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

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
    check_in: time | None = None
    check_out: time | None = None
    is_late: bool = False
    leave_request_id: UUID | None = None
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
    late: int = 0


class DailyRow(BaseModel):
    employee_id: UUID
    full_name: str
    employee_code: str | None = None
    staff_type: str
    department_name: str | None = None
    designation_name: str | None = None
    employee_active: bool
    on_approved_leave: bool = False
    record: AttendanceOut | None = None


class DailySheet(BaseModel):
    attendance_date: date
    is_working_day: bool = True
    non_working_reason: str | None = None
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
    working_days: int = 0
    totals: AttendanceCounts
    employees: list[EmployeeAttendanceSummary]


class AuditEntry(BaseModel):
    action: str
    message: str | None = None
    actor_name: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime
