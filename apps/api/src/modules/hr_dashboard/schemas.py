from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from src.modules.staff_attendance.schemas import AttendanceCounts, DailySummary


class Headcount(BaseModel):
    total: int
    active: int
    inactive: int
    teaching: int
    non_teaching: int


class DepartmentHeadcount(BaseModel):
    department_id: UUID | None
    name: str
    active: int
    inactive: int


class AttendanceMonth(BaseModel):
    date_from: date
    date_to: date
    totals: AttendanceCounts


class LeaveSummary(BaseModel):
    pending: int
    on_leave_today: int
    approved_next_7_days: int


class RecordsSummary(BaseModel):
    open_requests: int
    draft_letters: int
    issued_letters: int
    active_documents: int
    checklist_required: list[str]
    checklist_complete: int
    checklist_incomplete: int


class WorkflowSummary(BaseModel):
    onboarding_active: int
    offboarding_active: int
    tasks_total: int
    tasks_done: int
    overdue_tasks: int


class SalarySlipSummary(BaseModel):
    pay_periods: int
    latest_label: str | None
    latest_period_start: date | None
    latest_period_end: date | None
    latest_slips: int
    latest_paid: int


class HrAlert(BaseModel):
    key: Literal["leave", "attendance", "documents", "requests", "onboarding", "letters"]
    severity: Literal["info", "warning"]
    count: int
    message: str


class HrDashboardOut(BaseModel):
    as_of: date
    school_timezone: str
    generated_at: datetime
    headcount: Headcount
    departments: list[DepartmentHeadcount]
    attendance_today: DailySummary
    attendance_month: AttendanceMonth
    leave: LeaveSummary
    records: RecordsSummary
    workflows: WorkflowSummary
    salary_slips: SalarySlipSummary
    alerts: list[HrAlert]
