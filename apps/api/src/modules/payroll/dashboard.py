"""Payroll dashboard summaries. Every query is scoped to the caller's school."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError
from src.db.models import (
    ComplianceEvent,
    Department,
    Designation,
    Employee,
    EmployeeSalaryAssignment,
    EmployeeStatutoryProfile,
    PayrollPayslip,
    PayrollPayslipLine,
    PayrollRun,
    SalaryTemplate,
    StaffAttendance,
    StaffAttendanceRegularisation,
)
from src.modules.payroll.permissions import DASHBOARD
from src.modules.payroll.service import PayrollService, dump

ZERO = Decimal("0.00")
TWOPLACES = Decimal("0.01")
PROCESSED = ("APPROVED", "PUBLISHED", "PAID", "LOCKED")
PENDING_RUN = ("DRAFT", "PENDING_APPROVAL")
EXCLUDED_RUN = ("REJECTED", "CANCELLED")
PIPELINE = (
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "PUBLISHED",
    "LOCKED",
    "PAID",
    "CANCELLED",
)
STATUTORY_DUE = (
    ("PF", "Provident fund", 15, ("PF", "VPF"), ("PF_EMPLOYER",)),
    ("ESI", "ESI", 15, ("ESI",), ("ESI_EMPLOYER",)),
    ("PT", "Professional tax", 20, ("PT",), ()),
    ("TDS", "Tax deducted at source", 7, ("TDS",), ()),
)


def money(value) -> str:
    return str(Decimal(value or 0).quantize(TWOPLACES))


def month_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    last = calendar.monthrange(today.year, today.month)[1]
    return today.replace(day=1), date(today.year, today.month, last)


def trailing_months(end: date, count: int = 12) -> list[tuple[date, date]]:
    year, month = end.year, end.month
    found: list[tuple[date, date]] = []
    for _ in range(count):
        last = calendar.monthrange(year, month)[1]
        found.append((date(year, month, 1), date(year, month, last)))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    found.reverse()
    return found


def statutory_due_on(period_end: date, day: int) -> date:
    """Due date in the month after the pay period."""
    year = period_end.year + (1 if period_end.month == 12 else 0)
    month = 1 if period_end.month == 12 else period_end.month + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


class PayrollDashboard:
    def __init__(self, payroll: PayrollService):
        self.payroll = payroll
        self.db: AsyncSession = payroll.db
        self.school_id = payroll.school_id

    async def _gate(self) -> None:
        await self.payroll._allow(DASHBOARD)

    def _window(self, start: date | None, end: date | None) -> tuple[date, date]:
        if start is None and end is None:
            return month_bounds()
        if start is None or end is None or end < start:
            raise AppError("Provide both period_start and period_end, with period_end on or after period_start", status_code=422)
        return start, end

    async def summary(self, start: date | None, end: date | None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        return {
            "period": await self.period(start, end),
            "pipeline": await self.pipeline(start, end),
            "payslips": await self.payslips(start, end),
            "cost": await self.cost(start, end),
            "components": await self.components(start, end),
            "trend": await self.trend(end),
            "statutory": await self.statutory(start, end),
            "attendance": await self.attendance(start, end),
            "templates": await self.templates(),
            "pending": await self.pending(),
        }

    async def period(self, start: date | None, end: date | None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        run_ids = await self._run_ids(start, end)
        processed = await self._bucket(run_ids, PROCESSED)
        pending_runs = await self._bucket(run_ids, PENDING_RUN)
        unassigned = await self._unassigned(start, end)
        pending_runs["headcount"] += unassigned
        pending_runs["unassigned_headcount"] = unassigned
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "processed": processed,
            "pending": pending_runs,
        }

    async def pipeline(self, start: date | None = None, end: date | None = None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        rows = (
            await self.db.execute(
                select(PayrollRun.approval_status, func.count(PayrollRun.id))
                .where(*self._run_filters(start, end))
                .group_by(PayrollRun.approval_status)
            )
        ).all()
        counts = {status: 0 for status in PIPELINE}
        for status, count in rows:
            counts[status or "DRAFT"] = int(count)
        return {"period_start": start.isoformat(), "period_end": end.isoformat(), "counts": counts}

    async def payslips(self, start: date | None, end: date | None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        slip = PayrollPayslip
        run = PayrollRun
        base = (
            select(slip, run.approval_status)
            .join(run, run.id == slip.payroll_run_id)
            .where(
                slip.school_id == self.school_id,
                slip.status != "CANCELLED",
                *self._run_filters(start, end),
                run.approval_status.notin_(EXCLUDED_RUN),
            )
        )
        rows = (await self.db.execute(base)).all()
        status_counts = {"DRAFT": 0, "PUBLISHED": 0, "PAID": 0, "CANCELLED": 0}
        generated = published = viewed = downloaded = 0
        for item, _approval in rows:
            generated += 1
            status_counts[item.status] = status_counts.get(item.status, 0) + 1
            if item.status in {"PUBLISHED", "PAID"}:
                published += 1
            if int(item.view_count or 0) > 0:
                viewed += 1
            if int(item.download_count or 0) > 0:
                downloaded += 1
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "generated": generated,
            "published": published,
            "viewed": viewed,
            "downloaded": downloaded,
            "by_status": status_counts,
        }

    async def cost(self, start: date | None, end: date | None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "by_department": await self._cost_group(start, end, "department"),
            "by_branch": await self._cost_group(start, end, "branch"),
            "by_designation": await self._cost_group(start, end, "designation"),
        }

    async def components(self, start: date | None, end: date | None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        rows = (
            await self.db.execute(
                select(
                    PayrollPayslipLine.kind,
                    PayrollPayslipLine.code,
                    PayrollPayslipLine.name,
                    func.coalesce(func.sum(PayrollPayslipLine.amount), 0),
                )
                .join(PayrollPayslip, PayrollPayslip.id == PayrollPayslipLine.payslip_id)
                .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                .where(
                    PayrollPayslipLine.school_id == self.school_id,
                    PayrollPayslip.status != "CANCELLED",
                    PayrollRun.approval_status.notin_(EXCLUDED_RUN),
                    *self._run_filters(start, end),
                )
                .group_by(PayrollPayslipLine.kind, PayrollPayslipLine.code, PayrollPayslipLine.name)
                .order_by(PayrollPayslipLine.kind, PayrollPayslipLine.code)
            )
        ).all()
        earnings, deductions, employer = [], [], []
        for kind, code, name, amount in rows:
            item = {"code": code, "name": name, "amount": money(amount)}
            if kind == "EARNING":
                earnings.append(item)
            elif kind == "DEDUCTION":
                deductions.append(item)
            else:
                employer.append(item)
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "earnings": earnings,
            "deductions": deductions,
            "employer": employer,
        }

    async def trend(self, end: date | None = None) -> dict:
        await self._gate()
        anchor = end or date.today()
        months = []
        for start, finish in trailing_months(anchor, 12):
            row = (
                await self.db.execute(
                    select(
                        func.count(PayrollPayslip.id),
                        func.coalesce(func.sum(PayrollPayslip.gross_amount), 0),
                        func.coalesce(func.sum(PayrollPayslip.deductions_amount), 0),
                        func.coalesce(func.sum(PayrollPayslip.net_amount), 0),
                    )
                    .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                    .where(
                        PayrollPayslip.school_id == self.school_id,
                        PayrollPayslip.status != "CANCELLED",
                        PayrollRun.school_id == self.school_id,
                        PayrollRun.run_kind == "STATUTORY",
                        PayrollRun.approval_status.notin_(EXCLUDED_RUN),
                        PayrollRun.period_start >= start,
                        PayrollRun.period_start <= finish,
                    )
                )
            ).one()
            months.append(
                {
                    "month": start.strftime("%Y-%m"),
                    "headcount": int(row[0] or 0),
                    "gross": money(row[1]),
                    "deductions": money(row[2]),
                    "net": money(row[3]),
                }
            )
        return {"months": months}

    async def statutory(self, start: date | None, end: date | None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        totals = await self._line_totals(start, end)
        items = []
        for code, label, day, employee_codes, employer_codes in STATUTORY_DUE:
            employee_share = sum((totals.get(item, ZERO) for item in employee_codes), ZERO)
            employer_share = sum((totals.get(item, ZERO) for item in employer_codes), ZERO)
            items.append(
                {
                    "code": code,
                    "label": label,
                    "due_on": statutory_due_on(end, day).isoformat(),
                    "employee_share": money(employee_share),
                    "employer_share": money(employer_share),
                    "amount": money(employee_share + employer_share),
                }
            )
        events = list(
            (
                await self.db.execute(
                    select(ComplianceEvent)
                    .where(ComplianceEvent.school_id == self.school_id, ComplianceEvent.due_on >= start)
                    .order_by(ComplianceEvent.due_on)
                    .limit(24)
                )
            ).scalars()
        )
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "dues": items,
            "calendar": [dump(event) for event in events],
            "note": "PF and ESI are due on the 15th of the next month, professional tax on the 20th, and salary TDS on the 7th. EPS is already inside the employer PF share.",
        }

    async def attendance(self, start: date | None, end: date | None) -> dict:
        await self._gate()
        start, end = self._window(start, end)
        totals = (
            await self.db.execute(
                select(
                    func.count(PayrollPayslip.id),
                    func.coalesce(func.sum(PayrollPayslip.working_days), 0),
                    func.coalesce(func.sum(PayrollPayslip.lop_days), 0),
                    func.coalesce(func.sum(PayrollPayslip.paid_days), 0),
                    func.count(PayrollPayslip.id).filter(PayrollPayslip.lop_days > 0),
                )
                .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                .where(
                    PayrollPayslip.school_id == self.school_id,
                    PayrollPayslip.status != "CANCELLED",
                    PayrollRun.approval_status.notin_(EXCLUDED_RUN),
                    *self._run_filters(start, end),
                )
            )
        ).one()
        marks = (
            await self.db.execute(
                select(StaffAttendance.status, func.count(StaffAttendance.id))
                .where(
                    StaffAttendance.school_id == self.school_id,
                    StaffAttendance.attendance_date >= start,
                    StaffAttendance.attendance_date <= end,
                )
                .group_by(StaffAttendance.status)
            )
        ).all()
        by_status = {status: int(count) for status, count in marks}
        return {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "payslips": int(totals[0] or 0),
            "working_days": money(totals[1]),
            "lop_days": money(totals[2]),
            "paid_days": money(totals[3]),
            "employees_with_lop": int(totals[4] or 0),
            "marks": {
                "PRESENT": by_status.get("PRESENT", 0),
                "ABSENT": by_status.get("ABSENT", 0),
                "HALF_DAY": by_status.get("HALF_DAY", 0),
                "LEAVE": by_status.get("LEAVE", 0),
            },
        }

    async def templates(self) -> dict:
        await self._gate()
        today = date.today()
        rows = (
            await self.db.execute(
                select(
                    SalaryTemplate.id,
                    SalaryTemplate.name,
                    SalaryTemplate.grade_code,
                    SalaryTemplate.is_active,
                    func.count(EmployeeSalaryAssignment.id),
                )
                .outerjoin(
                    EmployeeSalaryAssignment,
                    (EmployeeSalaryAssignment.template_id == SalaryTemplate.id)
                    & (EmployeeSalaryAssignment.effective_from <= today)
                    & (
                        EmployeeSalaryAssignment.effective_to.is_(None)
                        | (EmployeeSalaryAssignment.effective_to >= today)
                    ),
                )
                .where(SalaryTemplate.school_id == self.school_id)
                .group_by(SalaryTemplate.id, SalaryTemplate.name, SalaryTemplate.grade_code, SalaryTemplate.is_active)
                .order_by(SalaryTemplate.name)
            )
        ).all()
        return {
            "templates": [
                {
                    "template_id": str(template_id),
                    "name": name,
                    "grade_code": grade_code,
                    "is_active": is_active,
                    "assigned_employees": int(count or 0),
                }
                for template_id, name, grade_code, is_active, count in rows
            ]
        }

    async def pending(self) -> dict:
        await self._gate()
        runs = list(
            (
                await self.db.execute(
                    select(PayrollRun)
                    .where(
                        PayrollRun.school_id == self.school_id,
                        PayrollRun.run_kind == "STATUTORY",
                        PayrollRun.approval_status == "PENDING_APPROVAL",
                    )
                    .order_by(PayrollRun.submitted_at.desc().nulls_last())
                    .limit(50)
                )
            ).scalars()
        )
        approvals = []
        for run in runs:
            totals = (
                await self.db.execute(
                    select(
                        func.count(PayrollPayslip.id),
                        func.coalesce(func.sum(PayrollPayslip.net_amount), 0),
                    ).where(
                        PayrollPayslip.payroll_run_id == run.id,
                        PayrollPayslip.status != "CANCELLED",
                    )
                )
            ).one()
            approvals.append(
                {
                    "run_id": str(run.id),
                    "label": run.label,
                    "period_start": run.period_start.isoformat(),
                    "period_end": run.period_end.isoformat(),
                    "submitted_at": run.submitted_at.isoformat() if run.submitted_at else None,
                    "headcount": int(totals[0] or 0),
                    "net": money(totals[1]),
                }
            )
        requests = (
            await self.db.execute(
                select(StaffAttendanceRegularisation, Employee.full_name, Employee.employee_code)
                .join(Employee, Employee.id == StaffAttendanceRegularisation.employee_id)
                .where(
                    StaffAttendanceRegularisation.school_id == self.school_id,
                    StaffAttendanceRegularisation.status == "PENDING",
                )
                .order_by(StaffAttendanceRegularisation.created_at.desc())
                .limit(50)
            )
        ).all()
        return {
            "approvals": approvals,
            "regularisations": [
                {
                    "id": str(item.id),
                    "employee_id": str(item.employee_id),
                    "employee_name": name,
                    "employee_code": code,
                    "attendance_date": item.attendance_date.isoformat(),
                    "reason": item.reason,
                    "requested_check_in": item.requested_check_in.isoformat(),
                    "requested_check_out": item.requested_check_out.isoformat(),
                }
                for item, name, code in requests
            ],
        }

    def _run_filters(self, start: date, end: date):
        return (
            PayrollRun.school_id == self.school_id,
            PayrollRun.run_kind == "STATUTORY",
            PayrollRun.period_start <= end,
            PayrollRun.period_end >= start,
        )

    async def _run_ids(self, start: date, end: date) -> list[UUID]:
        exact = list(
            (
                await self.db.execute(
                    select(PayrollRun.id).where(
                        PayrollRun.school_id == self.school_id,
                        PayrollRun.run_kind == "STATUTORY",
                        PayrollRun.period_start == start,
                        PayrollRun.period_end == end,
                    )
                )
            ).scalars()
        )
        if exact:
            return exact
        return list(
            (await self.db.execute(select(PayrollRun.id).where(*self._run_filters(start, end)))).scalars()
        )

    async def _bucket(self, run_ids: list[UUID], statuses: tuple[str, ...]) -> dict:
        if not run_ids:
            return {"headcount": 0, "gross": money(0), "deductions": money(0), "net": money(0)}
        row = (
            await self.db.execute(
                select(
                    func.count(PayrollPayslip.id),
                    func.coalesce(func.sum(PayrollPayslip.gross_amount), 0),
                    func.coalesce(func.sum(PayrollPayslip.deductions_amount), 0),
                    func.coalesce(func.sum(PayrollPayslip.net_amount), 0),
                )
                .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                .where(
                    PayrollPayslip.payroll_run_id.in_(run_ids),
                    PayrollPayslip.status != "CANCELLED",
                    PayrollRun.approval_status.in_(statuses),
                )
            )
        ).one()
        return {
            "headcount": int(row[0] or 0),
            "gross": money(row[1]),
            "deductions": money(row[2]),
            "net": money(row[3]),
        }

    async def _unassigned(self, start: date, end: date) -> int:
        covered = (
            select(PayrollPayslip.employee_id)
            .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
            .where(
                PayrollPayslip.employee_id.is_not(None),
                PayrollPayslip.status != "CANCELLED",
                PayrollRun.approval_status.notin_(EXCLUDED_RUN),
                *self._run_filters(start, end),
            )
        )
        count = (
            await self.db.execute(
                select(func.count(Employee.id)).where(
                    Employee.school_id == self.school_id,
                    Employee.status == "ACTIVE",
                    Employee.id.notin_(covered),
                )
            )
        ).scalar_one()
        return int(count or 0)

    async def _cost_group(self, start: date, end: date, kind: str) -> list[dict]:
        if kind == "department":
            label = func.coalesce(Department.name, "Unassigned")
            extra = [Department.id]
            join_target = (Department, Department.id == Employee.department_id)
        elif kind == "designation":
            label = func.coalesce(Designation.name, "Unassigned")
            extra = [Designation.id]
            join_target = (Designation, Designation.id == Employee.designation_id)
        else:
            label = func.coalesce(func.nullif(EmployeeStatutoryProfile.branch_name, ""), "Unassigned")
            extra = []
            join_target = (EmployeeStatutoryProfile, EmployeeStatutoryProfile.employee_id == Employee.id)
        rows = (
            await self.db.execute(
                select(
                    label,
                    func.count(PayrollPayslip.id),
                    func.coalesce(func.sum(PayrollPayslip.gross_amount), 0),
                    func.coalesce(func.sum(PayrollPayslip.deductions_amount), 0),
                    func.coalesce(func.sum(PayrollPayslip.net_amount), 0),
                    *extra,
                )
                .select_from(PayrollPayslip)
                .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                .join(Employee, Employee.id == PayrollPayslip.employee_id)
                .outerjoin(*join_target)
                .where(
                    PayrollPayslip.school_id == self.school_id,
                    PayrollPayslip.status != "CANCELLED",
                    PayrollRun.approval_status.notin_(EXCLUDED_RUN),
                    *self._run_filters(start, end),
                )
                .group_by(label, *extra)
                .order_by(label)
            )
        ).all()
        grouped = []
        for row in rows:
            grouped.append(
                {
                    "name": row[0],
                    "headcount": int(row[1] or 0),
                    "gross": money(row[2]),
                    "deductions": money(row[3]),
                    "net": money(row[4]),
                }
            )
        return grouped

    async def _line_totals(self, start: date, end: date) -> dict[str, Decimal]:
        rows = (
            await self.db.execute(
                select(PayrollPayslipLine.code, func.coalesce(func.sum(PayrollPayslipLine.amount), 0))
                .join(PayrollPayslip, PayrollPayslip.id == PayrollPayslipLine.payslip_id)
                .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                .where(
                    PayrollPayslipLine.school_id == self.school_id,
                    PayrollPayslip.status != "CANCELLED",
                    PayrollRun.approval_status.notin_(EXCLUDED_RUN),
                    PayrollPayslipLine.code.in_(("PF", "VPF", "PF_EMPLOYER", "ESI", "ESI_EMPLOYER", "PT", "TDS")),
                    *self._run_filters(start, end),
                )
                .group_by(PayrollPayslipLine.code)
            )
        ).all()
        return {code: Decimal(amount or 0) for code, amount in rows}
