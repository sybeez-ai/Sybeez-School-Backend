"""Read helpers other HR modules use to resolve employees within one school."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.db.models import Department, Designation, Employee, Teacher, User
from src.modules.employees.service import (
    employee_active_expr,
    employee_code_expr,
    employee_name_expr,
    sync_teacher_employees,
)


@dataclass
class EmployeeBrief:
    id: UUID
    full_name: str
    employee_code: str | None
    department_name: str | None
    designation_name: str | None
    staff_type: str
    user_id: UUID | None
    email: str | None
    joining_date: date | None
    title: str | None
    is_active: bool
    exit_date: date | None = None


def _brief_stmt(school_id: UUID) -> Select:
    return (
        select(
            Employee.id,
            employee_name_expr(),
            employee_code_expr(),
            Department.name,
            Designation.name,
            Employee.staff_type,
            func.coalesce(Teacher.user_id, Employee.user_id),
            func.coalesce(User.email, Employee.email),
            func.coalesce(Teacher.joining_date, Employee.joining_date),
            Teacher.title,
            employee_active_expr(),
            Employee.exit_date,
        )
        .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
        .outerjoin(User, User.id == Teacher.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .where(Employee.school_id == school_id)
    )


def _row_to_brief(row) -> EmployeeBrief:
    eid, name, code, dept, desig, staff_type, user_id, email, joined, title, active, exited = (
        row
    )
    return EmployeeBrief(
        id=eid,
        full_name=name or "—",
        employee_code=code,
        department_name=dept,
        designation_name=desig,
        staff_type=staff_type,
        user_id=user_id,
        email=email,
        joining_date=joined,
        title=title,
        is_active=bool(active),
        exit_date=exited,
    )


async def get_employee_brief(
    db: AsyncSession,
    school_id: UUID,
    employee_id: UUID,
    *,
    require_active: bool = False,
) -> EmployeeBrief:
    """Employee of this school, or 404 (another school's id is indistinguishable from a missing one)."""
    row = (
        await db.execute(_brief_stmt(school_id).where(Employee.id == employee_id))
    ).first()
    if row is None:
        raise NotFoundError("Employee not found")
    brief = _row_to_brief(row)
    if require_active and not brief.is_active:
        raise AppError(f"{brief.full_name} is inactive", status_code=422)
    return brief


async def employee_briefs(
    db: AsyncSession, school_id: UUID, ids: set[UUID]
) -> dict[UUID, EmployeeBrief]:
    if not ids:
        return {}
    rows = (await db.execute(_brief_stmt(school_id).where(Employee.id.in_(ids)))).all()
    return {b.id: b for b in map(_row_to_brief, rows)}


async def list_employee_briefs(
    db: AsyncSession, school_id: UUID, *, active_only: bool = False
) -> list[EmployeeBrief]:
    await sync_teacher_employees(db, school_id)
    stmt = _brief_stmt(school_id)
    if active_only:
        stmt = stmt.where(employee_active_expr())
    rows = (await db.execute(stmt.order_by(func.lower(employee_name_expr())))).all()
    return [_row_to_brief(r) for r in rows]


async def employee_for_user(
    db: AsyncSession, school_id: UUID, user_id: UUID
) -> EmployeeBrief:
    """The signed-in user's own employee record (teacher or linked staff login; 403 when there is none)."""
    await sync_teacher_employees(db, school_id)
    row = (
        await db.execute(
            _brief_stmt(school_id)
            .where(func.coalesce(Teacher.user_id, Employee.user_id) == user_id)
            .limit(1)
        )
    ).first()
    if row is None:
        raise ForbiddenError("No employee record is linked to your account")
    return _row_to_brief(row)


async def user_names(db: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    clean = {i for i in ids if i}
    if not clean:
        return {}
    rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(clean)))
    return {uid: name for uid, name in rows.all()}
