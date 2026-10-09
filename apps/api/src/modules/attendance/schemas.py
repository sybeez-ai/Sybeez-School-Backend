from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.shared.enums import AttendanceStatus, LeaveRequestStatus


class AttendanceMarkItem(BaseModel):
    student_id: UUID
    status: AttendanceStatus
    arrival_time: time | None = None
    reason: str | None = Field(default=None, max_length=1000)


class AttendanceMarkRequest(BaseModel):
    class_id: UUID
    attended_on: date
    marks: list[AttendanceMarkItem] = Field(min_length=1)


class AttendanceOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    student_name: str | None = None
    class_id: UUID
    class_name: str | None = None
    attended_on: date
    period: str | None = None
    status: str
    arrival_time: time | None = None
    reason: str | None = None
    leave_request_id: UUID | None = None
    marked_by: UUID | None = None
    marked_by_name: str | None = None
    marked_at: datetime | None = None
    last_modified_by: UUID | None = None
    last_modified_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RosterStudentOut(BaseModel):
    student_id: UUID
    full_name: str
    admission_no: str | None = None
    status: str | None = None
    attendance_id: UUID | None = None
    arrival_time: time | None = None
    reason: str | None = None


class AttendanceSummaryOut(BaseModel):
    present: int
    absent: int
    late: int
    excused: int
    half_day: int = 0
    unmarked: int
    total: int
    rate_percent: float | None = None


class MonthlyStatsOut(BaseModel):
    year: int
    month: int
    present: int
    absent: int
    late: int
    excused: int
    half_day: int
    total_marked: int
    rate_percent: float
    days: list[AttendanceOut]


class AuditLogOut(BaseModel):
    id: UUID
    attendance_id: UUID
    student_id: UUID
    student_name: str | None = None
    attended_on: date
    from_status: str | None
    to_status: str
    changed_by: UUID | None
    changed_by_name: str | None = None
    reason: str | None
    leave_request_id: UUID | None
    created_at: datetime


class LeaveRequestCreate(BaseModel):
    student_id: UUID | None = None
    start_date: date
    end_date: date
    reason: str = Field(min_length=1, max_length=2000)
    attachment_name: str | None = Field(default=None, max_length=255)
    attachment_url: str | None = None
    source: str = "MANUAL"

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class LeaveDecisionRequest(BaseModel):
    decision: LeaveRequestStatus
    decision_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision not in (LeaveRequestStatus.APPROVED, LeaveRequestStatus.REJECTED):
            raise ValueError("decision must be APPROVED or REJECTED")
        return self


class LeaveRequestOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    student_name: str | None = None
    class_name: str | None = None
    requested_by: UUID
    start_date: date
    end_date: date
    reason: str
    attachment_name: str | None = None
    attachment_url: str | None = None
    status: str
    source: str = "MANUAL"
    decided_by: UUID | None
    decision_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChildOut(BaseModel):
    student_id: UUID
    full_name: str
    admission_no: str | None = None
    class_id: UUID | None = None
    class_name: str | None = None
    attendance_rate: float | None = None


class ClassLowAttendanceOut(BaseModel):
    class_id: UUID
    class_name: str
    rate_percent: float
    present: int
    total: int


class OverviewOut(BaseModel):
    attended_on: date
    present: int
    absent: int
    late: int
    excused: int
    half_day: int
    total: int
    rate_percent: float
    low_classes: list[ClassLowAttendanceOut]


class StudentRateOut(BaseModel):
    student_id: UUID
    student_name: str
    class_name: str | None
    present: int
    absent: int
    late: int
    excused: int
    half_day: int
    total: int
    rate_percent: float


class ClassRateOut(BaseModel):
    class_id: UUID
    class_name: str
    present: int
    absent: int
    late: int
    excused: int
    half_day: int
    total: int
    rate_percent: float


class AttendanceSettingsOut(BaseModel):
    school_start_time: time
    absence_alert_days: int
    absence_alert_window_days: int
    late_alert_count: int
    low_attendance_percent: int
    teacher_can_approve_leave: bool
    notify_parents_on_absent: bool

    model_config = {"from_attributes": True}


class AttendanceSettingsUpdate(BaseModel):
    school_start_time: time | None = None
    absence_alert_days: int | None = Field(default=None, ge=1, le=30)
    absence_alert_window_days: int | None = Field(default=None, ge=7, le=90)
    late_alert_count: int | None = Field(default=None, ge=1, le=30)
    low_attendance_percent: int | None = Field(default=None, ge=50, le=100)
    teacher_can_approve_leave: bool | None = None
    notify_parents_on_absent: bool | None = None


class AlertOut(BaseModel):
    kind: str
    student_id: UUID
    student_name: str
    class_name: str | None
    message: str
    value: float | int
