"""Daily staff attendance for HRM employees (teaching and non-teaching).

Rules enforced here:
* Marks are only accepted on working days (HR settings weekdays, minus school holidays).
* A day covered by an APPROVED leave is owned by that leave: it is marked LEAVE when the leave is
  approved and cannot be overwritten or deleted by hand until the leave is cancelled.
* Every correction (edit, delete, bulk overwrite) is written to the audit log with before/after.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.db.models import (
    Department,
    Designation,
    Employee,
    StaffAttendance,
    StaffLeaveRequest,
    Teacher,
    User,
)
from src.modules.employees.service import (
    employee_active_expr,
    employee_code_expr,
    employee_name_expr,
    sync_teacher_employees,
)
from src.modules.hr.audit import hr_audit, hr_audit_trail
from src.modules.hr_leave_policy.service import (
    employee_join,
    non_working_reason,
    working_dates,
)
from src.modules.hr_settings.service import load_rules
from src.modules.staff_attendance.schemas import (
    TIMED_STATUSES,
    AttendanceBulk,
    AttendanceCounts,
    AttendanceMark,
    AttendanceOut,
    AttendanceUpdate,
    AuditEntry,
    BulkResult,
    DailyRow,
    DailySheet,
    DailySummary,
    EmployeeAttendanceSummary,
    RangeSummary,
)

MAX_RANGE_DAYS = 366
AUDIT_RESOURCE = "staff_attendance"
_STATUS_KEYS = {
    "PRESENT": "present",
    "ABSENT": "absent",
    "HALF_DAY": "half_day",
    "LEAVE": "leave",
}


def _fmt(d: date) -> str:
    return d.strftime("%d %b %Y")


def _assert_not_future(d: date) -> None:
    # One day of slack: the server's calendar date can lag the school's by a timezone offset.
    if d > date.today() + timedelta(days=1):
        raise AppError("Attendance cannot be marked for a future date", status_code=422)


def assert_range(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise AppError("date_from must be on or before date_to", status_code=422)
    if (date_to - date_from).days >= MAX_RANGE_DAYS:
        raise AppError(
            f"Date range cannot exceed {MAX_RANGE_DAYS} days", status_code=422
        )


def _snapshot(rec: StaffAttendance) -> dict:
    return {
        "status": rec.status,
        "check_in": rec.check_in,
        "check_out": rec.check_out,
        "notes": rec.notes,
    }


def _assert_times(status: str, check_in: time | None, check_out: time | None) -> None:
    if status not in TIMED_STATUSES and (check_in or check_out):
        raise AppError("Check-in/out times apply only to Present or Half day", status_code=422)
    if check_in and check_out and check_out <= check_in:
        raise AppError("Check-out must be after check-in", status_code=422)


_active_expr = employee_active_expr
_code_expr = employee_code_expr


class StaffAttendanceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── reads ──────────────────────────────────────────────────────────────

    async def daily(
        self,
        school_id: UUID,
        day: date,
        *,
        q: str | None = None,
        staff_type: str | None = None,
        department_id: UUID | None = None,
        status: str | None = None,
    ) -> DailySheet:
        """Active employees (plus anyone already marked that day) with their mark, if any."""
        await sync_teacher_employees(self.db, school_id)
        late_after = await self._late_after(school_id)
        marked_by = aliased(User)
        stmt = (
            self._filtered_employees(
                school_id, q=q, staff_type=staff_type, department_id=department_id
            )
            .add_columns(StaffAttendance, marked_by.full_name.label("marked_by_name"))
            .outerjoin(
                StaffAttendance,
                and_(
                    StaffAttendance.employee_id == Employee.id,
                    StaffAttendance.attendance_date == day,
                ),
            )
            .outerjoin(marked_by, marked_by.id == StaffAttendance.marked_by)
            .where(or_(_active_expr(), StaffAttendance.id.is_not(None)))
            .execution_options(populate_existing=True)
        )
        result = (await self.db.execute(stmt)).all()
        on_leave = await self._approved_leave_map(
            school_id, day, [r.employee_id for r in result]
        )
        rows = []
        summary = DailySummary()
        for r in result:
            rec: StaffAttendance | None = r.StaffAttendance
            record = (
                self._record_out(rec, r.full_name, r.employee_code, r.marked_by_name, late_after)
                if rec
                else None
            )
            rows.append(
                DailyRow(
                    employee_id=r.employee_id,
                    full_name=r.full_name or "—",
                    employee_code=r.employee_code,
                    staff_type=r.staff_type,
                    department_name=r.department_name,
                    designation_name=r.designation_name,
                    employee_active=bool(r.employee_active),
                    on_approved_leave=r.employee_id in on_leave,
                    record=record,
                )
            )
            summary.total += 1
            if rec:
                key = _STATUS_KEYS[rec.status]
                setattr(summary, key, getattr(summary, key) + 1)
                if record and record.is_late:
                    summary.late += 1
            else:
                summary.unmarked += 1
        if status:
            su = status.upper()
            rows = [
                row
                for row in rows
                if (
                    row.record is None
                    if su == "UNMARKED"
                    else row.record is not None and row.record.status == su
                )
            ]
        reason = await non_working_reason(self.db, school_id, day)
        return DailySheet(
            attendance_date=day,
            is_working_day=reason is None,
            non_working_reason=reason,
            summary=summary,
            rows=rows,
        )

    async def history(
        self,
        school_id: UUID,
        *,
        date_from: date,
        date_to: date,
        employee_id: UUID | None = None,
        status: str | None = None,
        q: str | None = None,
        staff_type: str | None = None,
        department_id: UUID | None = None,
    ) -> list[AttendanceOut]:
        assert_range(date_from, date_to)
        late_after = await self._late_after(school_id)
        stmt = self._records(school_id).where(
            StaffAttendance.attendance_date.between(date_from, date_to)
        )
        if employee_id:
            stmt = stmt.where(StaffAttendance.employee_id == employee_id)
        if status:
            stmt = stmt.where(StaffAttendance.status == status.upper())
        stmt = self._apply_filters(
            stmt, q=q, staff_type=staff_type, department_id=department_id
        )
        stmt = stmt.order_by(
            StaffAttendance.attendance_date.desc(), func.lower(employee_name_expr())
        )
        return [
            self._record_out(*row, late_after) for row in (await self.db.execute(stmt)).all()
        ]

    async def summary(
        self,
        school_id: UUID,
        *,
        date_from: date,
        date_to: date,
        q: str | None = None,
        staff_type: str | None = None,
        department_id: UUID | None = None,
    ) -> RangeSummary:
        assert_range(date_from, date_to)
        await sync_teacher_employees(self.db, school_id)
        counts = (
            select(
                StaffAttendance.employee_id,
                *(
                    func.count().filter(StaffAttendance.status == code).label(key)
                    for code, key in _STATUS_KEYS.items()
                ),
            )
            .where(
                StaffAttendance.school_id == school_id,
                StaffAttendance.attendance_date.between(date_from, date_to),
            )
            .group_by(StaffAttendance.employee_id)
            .subquery()
        )
        stmt = (
            self._filtered_employees(
                school_id, q=q, staff_type=staff_type, department_id=department_id
            )
            .add_columns(*(counts.c[key] for key in _STATUS_KEYS.values()))
            .outerjoin(counts, counts.c.employee_id == Employee.id)
            .where(or_(_active_expr(), counts.c.employee_id.is_not(None)))
        )
        totals = AttendanceCounts()
        employees = []
        for r in (await self.db.execute(stmt)).all():
            c = {key: int(getattr(r, key) or 0) for key in _STATUS_KEYS.values()}
            working = c["present"] + c["absent"] + c["half_day"]
            employees.append(
                EmployeeAttendanceSummary(
                    employee_id=r.employee_id,
                    full_name=r.full_name or "—",
                    employee_code=r.employee_code,
                    department_name=r.department_name,
                    marked_days=sum(c.values()),
                    attendance_rate=round(
                        (c["present"] + c["half_day"] / 2) * 100 / working, 1
                    )
                    if working
                    else None,
                    **c,
                )
            )
            for key, value in c.items():
                setattr(totals, key, getattr(totals, key) + value)
        return RangeSummary(
            date_from=date_from,
            date_to=date_to,
            working_days=len(await working_dates(self.db, school_id, date_from, date_to)),
            totals=totals,
            employees=employees,
        )

    async def audit_trail(self, school_id: UUID, record_id: UUID) -> list[AuditEntry]:
        """Corrections of a mark, newest first; still available after the mark was deleted."""
        rows = await hr_audit_trail(self.db, school_id, AUDIT_RESOURCE, record_id)
        if not rows:
            await self._get(school_id, record_id)
        return [AuditEntry(**row) for row in rows]

    # ── writes ─────────────────────────────────────────────────────────────

    async def mark(
        self, school_id: UUID, user_id: UUID, data: AttendanceMark
    ) -> AttendanceOut:
        _assert_not_future(data.attendance_date)
        await self._assert_working_day(school_id, data.attendance_date)
        emp = await self._employee(school_id, data.employee_id)
        if await self._existing_id(data.employee_id, data.attendance_date):
            raise self._duplicate(emp.full_name, data.attendance_date)
        if not emp.employee_active:
            raise AppError(
                f"{emp.full_name} is inactive; attendance can only be marked for active staff",
                status_code=422,
            )
        leave_id = (
            await self._approved_leave_map(school_id, data.attendance_date, [emp.employee_id])
        ).get(emp.employee_id)
        if leave_id:
            raise self._on_leave(emp.full_name, data.attendance_date)
        rec = StaffAttendance(
            school_id=school_id,
            employee_id=data.employee_id,
            attendance_date=data.attendance_date,
            status=data.status,
            notes=data.notes,
            check_in=data.check_in,
            check_out=data.check_out,
            marked_by=user_id,
        )
        self.db.add(rec)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError as exc:
            if "uq_staff_attendance_employee_date" in str(exc.orig):
                raise self._duplicate(emp.full_name, data.attendance_date) from exc
            raise
        return await self._get_out(school_id, rec.id)

    async def bulk(
        self, school_id: UUID, user_id: UUID, data: AttendanceBulk
    ) -> BulkResult:
        """Save a day sheet: create missing marks and overwrite existing ones."""
        day = data.attendance_date
        _assert_not_future(day)
        await self._assert_working_day(school_id, day)
        ids = {e.employee_id for e in data.entries}
        emps = {
            r.employee_id: r
            for r in (
                await self.db.execute(
                    self._employees(school_id).where(Employee.id.in_(ids))
                )
            ).all()
        }
        if ids - emps.keys():
            raise NotFoundError("Employee not found")
        existing = {
            rec.employee_id: rec
            for rec in (
                await self.db.execute(
                    select(StaffAttendance).where(
                        StaffAttendance.school_id == school_id,
                        StaffAttendance.attendance_date == day,
                        StaffAttendance.employee_id.in_(ids),
                    )
                )
            ).scalars()
        }
        inactive = sorted(
            emps[e.employee_id].full_name or "—"
            for e in data.entries
            if e.employee_id not in existing and not emps[e.employee_id].employee_active
        )
        if inactive:
            raise AppError(
                f"Inactive staff cannot be marked: {', '.join(inactive)}",
                status_code=422,
            )
        on_leave = await self._approved_leave_map(school_id, day, list(ids))
        blocked = sorted(
            emps[e.employee_id].full_name or "—"
            for e in data.entries
            if e.employee_id in on_leave and e.status != "LEAVE"
        )
        if blocked:
            raise ConflictError(
                f"On approved leave on {_fmt(day)} (cancel the leave to change this): "
                + ", ".join(blocked)
            )

        created = updated = 0
        corrections: list[tuple[StaffAttendance, dict]] = []
        for entry in data.entries:
            rec = existing.get(entry.employee_id)
            if rec is None:
                self.db.add(
                    StaffAttendance(
                        school_id=school_id,
                        employee_id=entry.employee_id,
                        attendance_date=day,
                        status=entry.status,
                        notes=entry.notes,
                        check_in=entry.check_in,
                        check_out=entry.check_out,
                        marked_by=user_id,
                        leave_request_id=on_leave.get(entry.employee_id),
                    )
                )
                created += 1
                continue
            before = _snapshot(rec)
            rec.status = entry.status
            if "notes" in entry.model_fields_set:
                rec.notes = entry.notes
            if {"check_in", "check_out"} & entry.model_fields_set or entry.status not in TIMED_STATUSES:
                rec.check_in, rec.check_out = (
                    (entry.check_in, entry.check_out) if entry.status in TIMED_STATUSES else (None, None)
                )
            rec.marked_by = user_id
            updated += 1
            if _snapshot(rec) != before:
                corrections.append((rec, before))
        await self.db.flush()
        for rec, before in corrections:
            await self._audit(
                rec,
                user_id,
                "corrected",
                before=before,
                after=_snapshot(rec),
                reason=data.correction_reason,
                via="day sheet",
            )
        late_after = await self._late_after(school_id)
        stmt = self._records(school_id).where(
            StaffAttendance.attendance_date == day, StaffAttendance.employee_id.in_(ids)
        )
        records = [
            self._record_out(*row, late_after) for row in (await self.db.execute(stmt)).all()
        ]
        return BulkResult(created=created, updated=updated, records=records)

    async def update(
        self, school_id: UUID, user_id: UUID, record_id: UUID, data: AttendanceUpdate
    ) -> AttendanceOut:
        rec = await self._get(school_id, record_id)
        if rec.leave_request_id and await self._leave_still_approved(rec.leave_request_id):
            raise ConflictError(
                "This day is covered by an approved leave. Cancel the leave to change the attendance."
            )
        before = _snapshot(rec)
        payload = data.model_dump(exclude_unset=True)
        if "status" in payload:
            if payload["status"] is None:
                raise AppError("Status is required", status_code=422)
            rec.status = payload["status"]
        if "notes" in payload:
            rec.notes = payload["notes"]
        if "check_in" in payload:
            rec.check_in = payload["check_in"]
        if "check_out" in payload:
            rec.check_out = payload["check_out"]
        if rec.status not in TIMED_STATUSES and not ({"check_in", "check_out"} & payload.keys()):
            rec.check_in = rec.check_out = None
        _assert_times(rec.status, rec.check_in, rec.check_out)
        rec.leave_request_id = None
        rec.marked_by = user_id
        await self.db.flush()
        after = _snapshot(rec)
        if after != before:
            await self._audit(rec, user_id, "corrected", before=before, after=after, reason=data.reason)
        return await self._get_out(school_id, rec.id)

    async def delete(
        self, school_id: UUID, record_id: UUID, user_id: UUID | None = None, reason: str | None = None
    ) -> None:
        rec = await self._get(school_id, record_id)
        if rec.leave_request_id and await self._leave_still_approved(rec.leave_request_id):
            raise ConflictError(
                "This day is covered by an approved leave. Cancel the leave to remove it."
            )
        await self._audit(rec, user_id, "deleted", before=_snapshot(rec), reason=reason)
        await self.db.delete(rec)
        await self.db.flush()

    # ── internals ──────────────────────────────────────────────────────────

    async def _assert_working_day(self, school_id: UUID, day: date) -> None:
        reason = await non_working_reason(self.db, school_id, day)
        if reason:
            raise AppError(f"{reason}; attendance is not taken", status_code=422)

    async def _late_after(self, school_id: UUID) -> time | None:
        rules = await load_rules(self.db, school_id)
        if rules.work_start_time is None:
            return None
        start = datetime.combine(date.min, rules.work_start_time)
        return (start + timedelta(minutes=rules.late_grace_minutes)).time()

    async def _approved_leave_map(
        self, school_id: UUID, day: date, employee_ids: list[UUID]
    ) -> dict[UUID, UUID]:
        """employee_id → approved leave id for employees on approved leave on ``day``."""
        if not employee_ids:
            return {}
        rows = await self.db.execute(
            select(Employee.id, StaffLeaveRequest.id)
            .select_from(StaffLeaveRequest)
            .outerjoin(
                Teacher,
                and_(
                    Teacher.user_id == StaffLeaveRequest.user_id,
                    Teacher.school_id == StaffLeaveRequest.school_id,
                ),
            )
            .join(Employee, employee_join(StaffLeaveRequest))
            .where(
                StaffLeaveRequest.school_id == school_id,
                StaffLeaveRequest.status == "APPROVED",
                StaffLeaveRequest.start_date <= day,
                StaffLeaveRequest.end_date >= day,
                Employee.id.in_(employee_ids),
            )
        )
        return dict(rows.all())

    async def _leave_still_approved(self, leave_id: UUID) -> bool:
        return (
            await self.db.scalar(
                select(StaffLeaveRequest.status).where(StaffLeaveRequest.id == leave_id)
            )
        ) == "APPROVED"

    async def _audit(
        self,
        rec: StaffAttendance,
        user_id: UUID | None,
        verb: str,
        *,
        before: dict | None = None,
        after: dict | None = None,
        reason: str | None = None,
        via: str | None = None,
    ) -> None:
        await hr_audit(
            self.db,
            school_id=rec.school_id,
            actor_id=user_id,
            action=f"hr.attendance.{verb}",
            resource_type=AUDIT_RESOURCE,
            resource_id=rec.id,
            message=f"Attendance for {_fmt(rec.attendance_date)} {verb}",
            details={
                "employee_id": rec.employee_id,
                "date": rec.attendance_date,
                "before": before,
                "after": after,
                "reason": reason,
                "via": via,
            },
        )

    @staticmethod
    def _employees(school_id: UUID) -> Select[Any]:
        return (
            select(
                Employee.id.label("employee_id"),
                employee_name_expr().label("full_name"),
                _code_expr().label("employee_code"),
                Employee.staff_type,
                Department.name.label("department_name"),
                Designation.name.label("designation_name"),
                _active_expr().label("employee_active"),
            )
            .select_from(Employee)
            .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
            .outerjoin(User, User.id == Teacher.user_id)
            .outerjoin(Department, Department.id == Employee.department_id)
            .outerjoin(Designation, Designation.id == Employee.designation_id)
            .where(Employee.school_id == school_id)
        )

    def _filtered_employees(
        self,
        school_id: UUID,
        *,
        q: str | None,
        staff_type: str | None,
        department_id: UUID | None,
    ) -> Select[Any]:
        stmt = self._apply_filters(
            self._employees(school_id),
            q=q,
            staff_type=staff_type,
            department_id=department_id,
        )
        return stmt.order_by(func.lower(employee_name_expr()))

    @staticmethod
    def _apply_filters(
        stmt: Select[Any],
        *,
        q: str | None,
        staff_type: str | None,
        department_id: UUID | None,
    ) -> Select[Any]:
        if staff_type:
            stmt = stmt.where(Employee.staff_type == staff_type.upper())
        if department_id:
            stmt = stmt.where(Employee.department_id == department_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(employee_name_expr().ilike(like), _code_expr().ilike(like))
            )
        return stmt

    @staticmethod
    def _records(school_id: UUID) -> Select[Any]:
        marked_by = aliased(User)
        return (
            select(
                StaffAttendance, employee_name_expr(), _code_expr(), marked_by.full_name
            )
            .join(Employee, Employee.id == StaffAttendance.employee_id)
            .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
            .outerjoin(User, User.id == Teacher.user_id)
            .outerjoin(marked_by, marked_by.id == StaffAttendance.marked_by)
            .where(StaffAttendance.school_id == school_id)
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _record_out(
        rec: StaffAttendance,
        name: str | None,
        code: str | None,
        marked_by_name: str | None,
        late_after: time | None = None,
    ) -> AttendanceOut:
        return AttendanceOut(
            id=rec.id,
            school_id=rec.school_id,
            employee_id=rec.employee_id,
            employee_name=name or "—",
            employee_code=code,
            attendance_date=rec.attendance_date,
            status=rec.status,
            notes=rec.notes,
            check_in=rec.check_in,
            check_out=rec.check_out,
            is_late=bool(
                late_after
                and rec.check_in
                and rec.status in TIMED_STATUSES
                and rec.check_in > late_after
            ),
            leave_request_id=rec.leave_request_id,
            marked_by=rec.marked_by,
            marked_by_name=marked_by_name,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )

    async def _employee(self, school_id: UUID, employee_id: UUID):
        row = (
            await self.db.execute(
                self._employees(school_id).where(Employee.id == employee_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Employee not found")
        return row

    async def _existing_id(self, employee_id: UUID, day: date) -> UUID | None:
        return await self.db.scalar(
            select(StaffAttendance.id).where(
                StaffAttendance.employee_id == employee_id,
                StaffAttendance.attendance_date == day,
            )
        )

    @staticmethod
    def _duplicate(name: str | None, day: date) -> ConflictError:
        return ConflictError(
            f"Attendance for {name or 'this employee'} on {_fmt(day)} is already marked. Edit the existing entry instead."
        )

    @staticmethod
    def _on_leave(name: str | None, day: date) -> ConflictError:
        return ConflictError(
            f"{name or 'This employee'} is on approved leave on {_fmt(day)}. Cancel the leave to change this."
        )

    async def _get(self, school_id: UUID, record_id: UUID) -> StaffAttendance:
        rec = (
            await self.db.execute(
                select(StaffAttendance).where(
                    StaffAttendance.id == record_id,
                    StaffAttendance.school_id == school_id,
                )
            )
        ).scalar_one_or_none()
        if rec is None:
            raise NotFoundError("Attendance record not found")
        return rec

    async def _get_out(self, school_id: UUID, record_id: UUID) -> AttendanceOut:
        row = (
            await self.db.execute(
                self._records(school_id).where(StaffAttendance.id == record_id)
            )
        ).one()
        return self._record_out(*row, await self._late_after(school_id))
