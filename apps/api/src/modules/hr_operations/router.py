"""HR operations API. Admin routes use require_hr_admin. Self routes use require_hr_or_self."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr.access import require_hr_admin, require_hr_or_self
from src.modules.hr_operations.service import HrOperations

router = APIRouter(prefix="/hr", tags=["hr-operations"])
Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Self = Annotated[CurrentUser, Depends(require_hr_or_self)]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


def _svc(db: AsyncSession) -> HrOperations:
    return HrOperations(db)


class ProbationIn(BaseModel):
    employee_id: UUID
    start_date: date
    end_date: date
    notes: str | None = None


class ProbationDecision(BaseModel):
    action: Literal["EXTEND", "CONFIRM", "TERMINATE"]
    extended_until: date | None = None
    confirmed_on: date | None = None
    notes: str | None = None


class CycleIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    period_start: date
    period_end: date


class GoalIn(BaseModel):
    cycle_id: UUID
    employee_id: UUID
    title: str = Field(min_length=2, max_length=200)
    description: str | None = None
    weight: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class ReviewIn(BaseModel):
    rating: Decimal = Field(ge=1, le=5)
    comments: str | None = None


class OpeningIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    department_id: UUID | None = None
    designation_id: UUID | None = None
    description: str | None = None
    openings: int = Field(default=1, ge=1, le=500)
    publish: bool = False


class ApplicationIn(BaseModel):
    opening_id: UUID
    applicant_name: str = Field(min_length=2, max_length=200)
    email: str | None = None
    phone: str | None = None


class InterviewIn(BaseModel):
    stage: Literal["SCREENING", "TECHNICAL", "HR", "FINAL"]
    scheduled_at: datetime | None = None
    interviewer_employee_id: UUID | None = None
    outcome: Literal["SCHEDULED", "PASSED", "FAILED", "NO_SHOW"] = "SCHEDULED"
    notes: str | None = None


class OfferIn(BaseModel):
    offered_on: date
    joining_date: date | None = None
    salary_note: str | None = None


class HireIn(BaseModel):
    staff_type: Literal["TEACHING", "NON_TEACHING"] = "NON_TEACHING"
    joining_date: date | None = None


class ProgramIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    provider: str | None = None
    description: str | None = None
    valid_months: int | None = Field(default=None, ge=1, le=120)


class TrainingIn(BaseModel):
    program_id: UUID
    employee_id: UUID
    completed_on: date | None = None
    certificate_no: str | None = None
    expires_on: date | None = None


class ShiftIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0, le=600)


class RosterIn(BaseModel):
    employee_id: UUID
    shift_id: UUID
    duty_date: date
    duty: Literal["TEACHING", "DUTY", "OFF"] = "DUTY"
    timetable_period: str | None = None
    overtime_minutes: int = Field(default=0, ge=0, le=24 * 60)
    notes: str | None = None


class AssetIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    asset_tag: str = Field(min_length=1, max_length=64)
    category: str | None = None
    serial_no: str | None = None
    cost: Decimal = Field(default=Decimal("0"), ge=0)


class IssueIn(BaseModel):
    employee_id: UUID
    issued_on: date
    condition_out: str | None = None
    notes: str | None = None


class ReturnIn(BaseModel):
    returned_on: date
    condition_in: str | None = None
    write_off: bool = False


class SettlementLineIn(BaseModel):
    kind: Literal["DUE", "RECOVERY"]
    label: str = Field(min_length=2, max_length=160)
    amount: Decimal = Field(ge=0)


class ExitIn(BaseModel):
    employee_id: UUID
    exit_date: date
    exit_reason: Literal["RESIGNATION", "TERMINATION", "RETIREMENT", "CONTRACT_END", "OTHER"]
    notes: str | None = None
    lines: list[SettlementLineIn] = Field(default_factory=list)


@router.get("/reports/{kind}")
async def export_report(
    kind: str,
    user: Admin,
    db: Db,
    format: str = Query(default="json"),
    date_from: date | None = None,
    date_to: date | None = None,
):
    payload = await _svc(db).report(_sid(user), user.id, kind, format, date_from, date_to)
    if isinstance(payload, dict):
        return payload
    body, media, filename = payload
    return Response(content=body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/probations/me")
async def my_probation(user: Self, db: Db):
    return await _svc(db).list_probation(_sid(user), user_id=user.id)


@router.get("/probations")
async def list_probation(user: Admin, db: Db):
    return await _svc(db).list_probation(_sid(user))


@router.post("/probations", status_code=201)
async def start_probation(body: ProbationIn, user: Admin, db: Db):
    return await _svc(db).start_probation(_sid(user), user.id, body.model_dump())


@router.post("/probations/{probation_id}/decide")
async def decide_probation(probation_id: UUID, body: ProbationDecision, user: Admin, db: Db):
    return await _svc(db).decide_probation(_sid(user), user.id, probation_id, body.model_dump())


@router.post("/appraisal-cycles", status_code=201)
async def create_cycle(body: CycleIn, user: Admin, db: Db):
    return await _svc(db).create_cycle(_sid(user), user.id, body.model_dump())


@router.post("/appraisal-cycles/{cycle_id}/open")
async def open_cycle(cycle_id: UUID, user: Admin, db: Db):
    return await _svc(db).open_cycle(_sid(user), user.id, cycle_id)


@router.post("/appraisal-goals", status_code=201)
async def add_goal(body: GoalIn, user: Admin, db: Db):
    return await _svc(db).add_goal(_sid(user), user.id, body.model_dump())


@router.get("/appraisals/me")
async def my_appraisals(user: Self, db: Db):
    return await _svc(db).list_appraisals(_sid(user), user_id=user.id)


@router.get("/appraisals")
async def list_appraisals(user: Admin, db: Db):
    return await _svc(db).list_appraisals(_sid(user))


@router.post("/appraisals/{appraisal_id}/self")
async def self_review(appraisal_id: UUID, body: ReviewIn, user: Self, db: Db):
    return await _svc(db).review(_sid(user), user, appraisal_id, "SELF", body.model_dump())


@router.post("/appraisals/{appraisal_id}/manager")
async def manager_review(appraisal_id: UUID, body: ReviewIn, user: Self, db: Db):
    return await _svc(db).review(_sid(user), user, appraisal_id, "MANAGER", body.model_dump())


@router.post("/appraisals/{appraisal_id}/final")
async def finalise_appraisal(appraisal_id: UUID, body: ReviewIn, user: Admin, db: Db):
    return await _svc(db).finalise(_sid(user), user.id, appraisal_id, body.model_dump())


@router.post("/openings", status_code=201)
async def create_opening(body: OpeningIn, user: Admin, db: Db):
    return await _svc(db).create_opening(_sid(user), user.id, body.model_dump())


@router.post("/applications", status_code=201)
async def add_application(body: ApplicationIn, user: Admin, db: Db):
    return await _svc(db).add_application(_sid(user), user.id, body.model_dump())


@router.post("/applications/{application_id}/interviews", status_code=201)
async def add_interview(application_id: UUID, body: InterviewIn, user: Admin, db: Db):
    return await _svc(db).add_interview(_sid(user), user.id, application_id, body.model_dump())


@router.post("/applications/{application_id}/offer", status_code=201)
async def make_offer(application_id: UUID, body: OfferIn, user: Admin, db: Db):
    return await _svc(db).make_offer(_sid(user), user.id, application_id, body.model_dump())


@router.post("/offers/{offer_id}/accept")
async def accept_offer(offer_id: UUID, user: Admin, db: Db):
    return await _svc(db).accept_offer(_sid(user), user.id, offer_id)


@router.post("/applications/{application_id}/hire")
async def hire(application_id: UUID, body: HireIn, user: Admin, db: Db):
    return await _svc(db).hire(_sid(user), user.id, application_id, body.model_dump())


@router.post("/training-programs", status_code=201)
async def create_program(body: ProgramIn, user: Admin, db: Db):
    return await _svc(db).create_program(_sid(user), user.id, body.model_dump())


@router.post("/training-records", status_code=201)
async def assign_training(body: TrainingIn, user: Admin, db: Db):
    return await _svc(db).assign_training(_sid(user), user.id, body.model_dump())


@router.get("/training/me")
async def my_training(user: Self, db: Db):
    return await _svc(db).my_training(_sid(user), user.id)


@router.post("/shifts", status_code=201)
async def create_shift(body: ShiftIn, user: Admin, db: Db):
    return await _svc(db).create_shift(_sid(user), user.id, body.model_dump())


@router.post("/roster", status_code=201)
async def assign_roster(body: RosterIn, user: Admin, db: Db):
    return await _svc(db).assign_roster(_sid(user), user.id, body.model_dump())


@router.get("/roster/me")
async def my_roster(user: Self, db: Db, date_from: date | None = None, date_to: date | None = None):
    return await _svc(db).list_roster(_sid(user), user_id=user.id, date_from=date_from, date_to=date_to)


@router.get("/roster/overtime")
async def overtime(user: Admin, db: Db, date_from: date | None = None, date_to: date | None = None):
    return await _svc(db).overtime(_sid(user), date_from, date_to)


@router.get("/roster")
async def list_roster(user: Admin, db: Db, date_from: date | None = None, date_to: date | None = None):
    return await _svc(db).list_roster(_sid(user), date_from=date_from, date_to=date_to)


@router.post("/assets", status_code=201)
async def create_asset(body: AssetIn, user: Admin, db: Db):
    return await _svc(db).create_asset(_sid(user), user.id, body.model_dump())


@router.post("/assets/{asset_id}/issue", status_code=201)
async def issue_asset(asset_id: UUID, body: IssueIn, user: Admin, db: Db):
    return await _svc(db).issue_asset(_sid(user), user.id, asset_id, body.model_dump())


@router.post("/asset-issues/{issue_id}/return")
async def return_asset(issue_id: UUID, body: ReturnIn, user: Admin, db: Db):
    return await _svc(db).return_asset(_sid(user), user.id, issue_id, body.model_dump())


@router.get("/assets/me")
async def my_assets(user: Self, db: Db):
    return await _svc(db).my_assets(_sid(user), user.id)


@router.post("/exits", status_code=201)
async def draft_exit(body: ExitIn, user: Admin, db: Db):
    return await _svc(db).draft_exit(_sid(user), user.id, body.model_dump())


@router.post("/exits/{case_id}/post")
async def post_exit(case_id: UUID, user: Admin, db: Db):
    return await _svc(db).post_exit(_sid(user), user.id, case_id)
