"""School admin directory and the morning dashboard."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.db.models import AdmissionApplication, Employee, FeeInvoice, StaffAttendance, Student
from src.modules.employees.schemas import EmployeeOut
from src.modules.employees.service import EmployeeService, sync_teacher_employees
from src.modules.fees.tenant import bind_tenant
from src.shared.enums import PersonStatus, UserRole

router = APIRouter(prefix="/admin", tags=["school-admin"])

AdminUser = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN))]
Db = Annotated[AsyncSession, Depends(get_db)]


class AdminDashboard(BaseModel):
    students_active: int
    teaching_staff: int
    non_teaching_staff: int
    open_admissions: int
    staff_present_today: int
    fee_outstanding: str


class StaffPage(BaseModel):
    items: list[EmployeeOut]
    total: int


async def _school(db: AsyncSession, user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id)
    return user.school_id


@router.get("/dashboard", response_model=AdminDashboard)
async def dashboard(user: AdminUser, db: Db) -> AdminDashboard:
    school_id = await _school(db, user)
    await sync_teacher_employees(db, school_id)
    today = date.today()
    students = await db.scalar(
        select(func.count())
        .select_from(Student)
        .where(Student.school_id == school_id, Student.status == PersonStatus.ACTIVE.value)
    )
    teaching = await db.scalar(
        select(func.count())
        .select_from(Employee)
        .where(
            Employee.school_id == school_id,
            Employee.staff_type == "TEACHING",
            Employee.status == "ACTIVE",
        )
    )
    non_teaching = await db.scalar(
        select(func.count())
        .select_from(Employee)
        .where(
            Employee.school_id == school_id,
            Employee.staff_type == "NON_TEACHING",
            Employee.status == "ACTIVE",
        )
    )
    admissions = await db.scalar(
        select(func.count())
        .select_from(AdmissionApplication)
        .where(
            AdmissionApplication.school_id == school_id,
            AdmissionApplication.status.in_(("APPLIED", "UNDER_REVIEW", "OFFERED")),
        )
    )
    present = await db.scalar(
        select(func.count())
        .select_from(StaffAttendance)
        .where(
            StaffAttendance.school_id == school_id,
            StaffAttendance.attendance_date == today,
            StaffAttendance.status == "PRESENT",
        )
    )
    outstanding = await db.scalar(
        select(func.coalesce(func.sum(FeeInvoice.balance_amount), 0)).where(
            FeeInvoice.school_id == school_id,
            FeeInvoice.status != "CANCELLED",
        )
    )
    return AdminDashboard(
        students_active=int(students or 0),
        teaching_staff=int(teaching or 0),
        non_teaching_staff=int(non_teaching or 0),
        open_admissions=int(admissions or 0),
        staff_present_today=int(present or 0),
        fee_outstanding=str(outstanding or 0),
    )


@router.get("/staff", response_model=StaffPage)
async def list_staff(
    user: AdminUser,
    db: Db,
    q: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, pattern="^(ACTIVE|INACTIVE|LEFT)$"),
    staff_type: str | None = Query(default=None, pattern="^(TEACHING|NON_TEACHING)$"),
    employment_type: str | None = Query(default=None, pattern="^(FULL_TIME|PART_TIME|CONTRACT|TEMPORARY)$"),
    department_id: UUID | None = None,
    designation_id: UUID | None = None,
    has_login: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> StaffPage:
    """Every teacher and non-teaching employee, from the HR employee record."""
    school_id = await _school(db, user)
    items, total = await EmployeeService(db).list(
        school_id,
        q=q,
        status=status,
        staff_type=staff_type,
        employment_type=employment_type,
        department_id=department_id,
        designation_id=designation_id,
        limit=None,
        offset=0,
    )
    if has_login is not None:
        items = [row for row in items if row.has_login is has_login]
        total = len(items)
    page = items[offset : offset + limit]
    if has_login is None:
        return StaffPage(items=page if limit else items, total=total)
    return StaffPage(items=page, total=total)
