"""Daily staff attendance for HRM employees (teaching and non-teaching)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.db.models import (
    Department,
    Designation,
    Employee,
    StaffAttendance,
    Teacher,
    User,
)
from src.modules.employees.service import employee_name_expr, sync_teacher_employees
from src.modules.staff_attendance.schemas import (
    AttendanceBulk,
    AttendanceCounts,
    AttendanceMark,
    AttendanceOut,
    AttendanceUpdate,
    BulkResult,
    DailyRow,
    DailySheet,
    DailySummary,
    EmployeeAttendanceSummary,
    RangeSummary,
)
from src.shared.enums import PersonStatus

MAX_RANGE_DAYS = 366
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


def _active_expr():
    """Whether an employee can be marked; needs ``Teacher`` and ``User`` outer-joined."""
    return case(
        (
            Employee.staff_type == "TEACHING",
            and_(
                Teacher.status == PersonStatus.ACTIVE.value,
                func.coalesce(User.is_active, True),
            ),
        ),
        else_=Employee.status == "ACTIVE",
    )


def _code_expr():
    return func.coalesce(
        Teacher.employee_code, Teacher.teacher_code, Employee.employee_code
    )


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
        rows = []
        summary = DailySummary()
        for r in (await self.db.execute(stmt)).all():
            rec: StaffAttendance | None = r.StaffAttendance
            record = (
                self._record_out(rec, r.full_name, r.employee_code, r.marked_by_name)
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
                    record=record,
                )
            )
            summary.total += 1
            if rec:
                key = _STATUS_KEYS[rec.status]
                setattr(summary, key, getattr(summary, key) + 1)
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
        return DailySheet(attendance_date=day, summary=summary, rows=rows)

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
        return [self._record_out(*row) for row in (await self.db.execute(stmt)).all()]

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
            date_from=date_from, date_to=date_to, totals=totals, employees=employees
        )

    # ── writes ─────────────────────────────────────────────────────────────

    async def mark(
        self, school_id: UUID, user_id: UUID, data: AttendanceMark
    ) -> AttendanceOut:
        _assert_not_future(data.attendance_date)
        emp = await self._employee(school_id, data.employee_id)
        if await self._existing_id(data.employee_id, data.attendance_date):
            raise self._duplicate(emp.full_name, data.attendance_date)
        if not emp.employee_active:
            raise AppError(
                f"{emp.full_name} is inactive; attendance can only be marked for active staff",
                status_code=422,
            )
        rec = StaffAttendance(
            school_id=school_id,
            employee_id=data.employee_id,
            attendance_date=data.attendance_date,
            status=data.status,
            notes=data.notes,
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

        created = updated = 0
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
                        marked_by=user_id,
                    )
                )
                created += 1
            else:
                rec.status = entry.status
                if "notes" in entry.model_fields_set:
                    rec.notes = entry.notes
                rec.marked_by = user_id
                updated += 1
        await self.db.flush()
        stmt = self._records(school_id).where(
            StaffAttendance.attendance_date == day, StaffAttendance.employee_id.in_(ids)
        )
        records = [
            self._record_out(*row) for row in (await self.db.execute(stmt)).all()
        ]
        return BulkResult(created=created, updated=updated, records=records)

    async def update(
        self, school_id: UUID, user_id: UUID, record_id: UUID, data: AttendanceUpdate
    ) -> AttendanceOut:
        rec = await self._get(school_id, record_id)
        payload = data.model_dump(exclude_unset=True)
        if "status" in payload:
            if payload["status"] is None:
                raise AppError("Status is required", status_code=422)
            rec.status = payload["status"]
        if "notes" in payload:
            rec.notes = payload["notes"]
        rec.marked_by = user_id
        await self.db.flush()
        return await self._get_out(school_id, rec.id)

    async def delete(self, school_id: UUID, record_id: UUID) -> None:
        rec = await self._get(school_id, record_id)
        await self.db.delete(rec)
        await self.db.flush()

    # ── internals ──────────────────────────────────────────────────────────

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
        return self._record_out(*row)
