from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError, NotFoundError
from src.modules.employees.lookup import get_employee_brief
from src.modules.hr.audit import SELF_SERVICE_ROLES
from src.modules.hr_leave_policy.schemas import (
    EmployeeBalances,
    HolidayIn,
    HolidayOut,
    LeaveCalendar,
    LeaveTypeIn,
    LeaveTypeOut,
    LeaveTypeUpdate,
    WorkingDays,
)
from src.modules.hr_leave_policy.service import LeavePolicyService
from src.shared.enums import UserRole
from src.shared.utils import utcnow

router = APIRouter(prefix="/hr", tags=["hr-leave-policy"])

Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]
Anyone = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, *SELF_SERVICE_ROLES))]
SelfService = Annotated[CurrentUser, Depends(require_roles(*SELF_SERVICE_ROLES))]
Db = Annotated[AsyncSession, Depends(get_db)]
Year = Annotated[int | None, Query(ge=2000, le=2100)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/leave-types", response_model=list[LeaveTypeOut])
async def list_leave_types(user: Anyone, db: Db, include_inactive: bool = False):
    active_only = not (include_inactive and user.role == UserRole.SCHOOL_ADMIN)
    return await LeavePolicyService(db).list_types(_sid(user), active_only=active_only)


@router.post("/leave-types", response_model=LeaveTypeOut, status_code=201)
async def create_leave_type(body: LeaveTypeIn, user: Admin, db: Db):
    return await LeavePolicyService(db).create_type(_sid(user), user.id, body)


@router.patch("/leave-types/{type_id}", response_model=LeaveTypeOut)
async def update_leave_type(type_id: UUID, body: LeaveTypeUpdate, user: Admin, db: Db):
    return await LeavePolicyService(db).update_type(_sid(user), user.id, type_id, body)


@router.delete("/leave-types/{type_id}", status_code=204)
async def delete_leave_type(type_id: UUID, user: Admin, db: Db):
    await LeavePolicyService(db).delete_type(_sid(user), user.id, type_id)
    return Response(status_code=204)


@router.get("/holidays", response_model=list[HolidayOut])
async def list_holidays(user: Anyone, db: Db, year: Year = None):
    return await LeavePolicyService(db).list_holidays(_sid(user), year)


@router.post("/holidays", response_model=HolidayOut, status_code=201)
async def add_holiday(body: HolidayIn, user: Admin, db: Db):
    return await LeavePolicyService(db).add_holiday(_sid(user), user.id, body)


@router.delete("/holidays/{holiday_id}", status_code=204)
async def delete_holiday(holiday_id: UUID, user: Admin, db: Db):
    await LeavePolicyService(db).delete_holiday(_sid(user), user.id, holiday_id)
    return Response(status_code=204)


@router.get("/working-days", response_model=WorkingDays)
async def working_days(user: Anyone, db: Db, start_date: date, end_date: date):
    return await LeavePolicyService(db).working_days(_sid(user), start_date, end_date)


@router.get("/leave/balances/me", response_model=EmployeeBalances)
async def my_leave_balances(user: SelfService, db: Db, year: Year = None):
    y = year or utcnow().year
    return EmployeeBalances(
        year=y,
        user_id=user.id,
        employee_name=user.full_name,
        balances=await LeavePolicyService(db).balances(_sid(user), user.id, y),
    )


@router.get("/leave/balances/{employee_id}", response_model=EmployeeBalances)
async def employee_leave_balances(employee_id: UUID, user: Admin, db: Db, year: Year = None):
    emp = await get_employee_brief(db, _sid(user), employee_id)
    if emp.user_id is None:
        raise NotFoundError(f"{emp.full_name} has no sign-in, so no leave is recorded")
    y = year or utcnow().year
    return EmployeeBalances(
        year=y,
        user_id=emp.user_id,
        employee_name=emp.full_name,
        balances=await LeavePolicyService(db).balances(_sid(user), emp.user_id, y),
    )


@router.get("/leave/calendar", response_model=LeaveCalendar)
async def leave_calendar(user: Admin, db: Db, date_from: date, date_to: date):
    return await LeavePolicyService(db).calendar(_sid(user), date_from, date_to)
