"""Staff self-attendance. Registered before the daily attendance router so these paths win."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.modules.hr.access import require_hr_admin, require_hr_or_self
from src.modules.payroll.ratelimit import payroll_rate_limit
from src.modules.payroll.schemas import (
    CheckIn,
    ManualAttendance,
    PeriodLock,
    RegularisationDecision,
    RegularisationIn,
    SessionCorrect,
)
from src.modules.payroll.service import dump
from src.modules.staff_attendance.self_service import StaffSelfAttendance

router = APIRouter(prefix="/hr/attendance", tags=["hr-attendance-self"])
Db = Annotated[AsyncSession, Depends(get_db)]


async def limited_self(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_hr_or_self)],
) -> CurrentUser:
    await payroll_rate_limit(request, user)
    return user


async def limited_admin(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
) -> CurrentUser:
    await payroll_rate_limit(request, user)
    return user


Self = Annotated[CurrentUser, Depends(limited_self)]
Admin = Annotated[CurrentUser, Depends(limited_admin)]


def _svc(db: AsyncSession, user: CurrentUser) -> StaffSelfAttendance:
    return StaffSelfAttendance(db, user)


@router.post("/check-in")
async def check_in(body: CheckIn, user: Self, db: Db):
    result = await _svc(db, user).check_in(body.model_dump())
    return {"session": dump(result["session"]), "already_open": result["already_open"]}


@router.post("/check-out")
async def check_out(body: CheckIn, user: Self, db: Db):
    return dump(await _svc(db, user).check_out(body.model_dump()))


@router.post("/manual", status_code=201)
async def manual_attendance(body: ManualAttendance, user: Self, db: Db):
    return dump(await _svc(db, user).manual(body.model_dump()))


@router.get("/sessions/me")
async def my_sessions(user: Self, db: Db, date_from: date, date_to: date):
    return [dump(row) for row in await _svc(db, user).list_mine(date_from, date_to)]


@router.get("/sessions")
async def school_sessions(user: Admin, db: Db, date_from: date, date_to: date, employee_id: UUID | None = None):
    return [dump(row) for row in await _svc(db, user).list_school(date_from, date_to, employee_id)]


@router.post("/sessions/{session_id}/correct")
async def correct_session(session_id: UUID, body: SessionCorrect, user: Admin, db: Db):
    return dump(await _svc(db, user).correct(session_id, body.model_dump()))


@router.post("/regularisations", status_code=201)
async def request_regularisation(body: RegularisationIn, user: Self, db: Db):
    return dump(await _svc(db, user).request_regularisation(body.model_dump()))


@router.get("/regularisations/me")
async def my_regularisations(user: Self, db: Db):
    return [dump(row) for row in await _svc(db, user).list_regularisations(True)]


@router.get("/regularisations")
async def school_regularisations(user: Admin, db: Db):
    return [dump(row) for row in await _svc(db, user).list_regularisations(False)]


@router.post("/regularisations/{item_id}/decide")
async def decide_regularisation(item_id: UUID, body: RegularisationDecision, user: Admin, db: Db):
    return dump(await _svc(db, user).decide_regularisation(item_id, body.approve, body.note))


@router.post("/periods/lock")
async def lock_period(body: PeriodLock, user: Admin, db: Db):
    result = await _svc(db, user).lock_period(body.period_start, body.period_end, body.reason)
    return {
        "lock_id": str(result["lock_id"]),
        "run_id": str(result["run_id"]),
        "created": result["created"],
        "skipped": result["skipped"],
    }
