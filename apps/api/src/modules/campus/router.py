"""Library, activities, events, and the school calendar."""

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.campus.service import CampusService
from src.modules.fees.tenant import bind_tenant
from src.shared.enums import UserRole

Reader = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT, UserRole.PARENT)),
]


async def _db(db: Annotated[AsyncSession, Depends(get_db)], user: Reader) -> AsyncSession:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id, user_id=user.id, role=user.role.value)
    return db


DB = Annotated[AsyncSession, Depends(_db)]
library_router = APIRouter(prefix="/library", tags=["library"])
activities_router = APIRouter(prefix="/activities", tags=["activities"])
calendar_router = APIRouter(prefix="/calendar", tags=["calendar"])


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    author: str | None = None
    isbn: str | None = Field(default=None, max_length=32)
    subject: str | None = None


class CopyIn(BaseModel):
    title_id: UUID
    barcode: str = Field(min_length=1, max_length=64)


class LoanIn(BaseModel):
    copy_id: UUID
    student_id: UUID
    issued_on: date
    due_on: date


class ReturnIn(BaseModel):
    returned_on: date
    fine_amount: Decimal | None = Field(default=None, ge=0)


class FineIn(BaseModel):
    loan_id: UUID
    amount: Decimal = Field(gt=0)
    note: str | None = Field(default=None, max_length=240)


class FineSettleIn(BaseModel):
    status: str


class ActivityIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    kind: str = "ACTIVITY"
    description: str | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    class_id: UUID | None = None
    place: str | None = None


class CalendarIn(BaseModel):
    kind: str
    title: str = Field(min_length=2, max_length=200)
    starts_on: date
    ends_on: date
    class_id: UUID | None = None
    notes: str | None = None


@library_router.get("/titles", dependencies=[Depends(require_permission("library.read"))])
async def list_titles(db: DB, user: Reader):
    return await CampusService(db).titles(user)


@library_router.post("/titles", status_code=201, dependencies=[Depends(require_permission("library.write"))])
async def create_title(body: TitleIn, db: DB, user: Reader):
    return await CampusService(db).add_title(user, body)


@library_router.get("/copies", dependencies=[Depends(require_permission("library.read"))])
async def list_copies(db: DB, user: Reader, title_id: UUID | None = None):
    return await CampusService(db).copies(user, title_id)


@library_router.post("/copies", status_code=201, dependencies=[Depends(require_permission("library.write"))])
async def create_copy(body: CopyIn, db: DB, user: Reader):
    return await CampusService(db).add_copy(user, body)


@library_router.get("/loans", dependencies=[Depends(require_permission("library.read"))])
async def list_loans(db: DB, user: Reader):
    return await CampusService(db).loans(user)


@library_router.post("/loans", status_code=201, dependencies=[Depends(require_permission("library.write"))])
async def create_loan(body: LoanIn, db: DB, user: Reader):
    return await CampusService(db).add_loan(user, body)


@library_router.post("/loans/{loan_id}/return", dependencies=[Depends(require_permission("library.write"))])
async def return_loan(loan_id: UUID, body: ReturnIn, db: DB, user: Reader):
    return await CampusService(db).return_loan(user, loan_id, body.returned_on, body.fine_amount)


@library_router.get("/fines", dependencies=[Depends(require_permission("library.read"))])
async def list_fines(db: DB, user: Reader):
    return await CampusService(db).fines(user)


@library_router.post("/fines", status_code=201, dependencies=[Depends(require_permission("library.write"))])
async def create_fine(body: FineIn, db: DB, user: Reader):
    return await CampusService(db).add_fine(user, body)


@library_router.post("/fines/{fine_id}/settle", dependencies=[Depends(require_permission("library.write"))])
async def settle_fine(fine_id: UUID, body: FineSettleIn, db: DB, user: Reader):
    return await CampusService(db).settle_fine(user, fine_id, body.status)


@activities_router.get("", dependencies=[Depends(require_permission("activities.read"))])
async def list_activities(db: DB, user: Reader):
    return await CampusService(db).activities(user)


@activities_router.post("", status_code=201, dependencies=[Depends(require_permission("activities.write"))])
async def create_activity(body: ActivityIn, db: DB, user: Reader):
    return await CampusService(db).add_activity(user, body)


@calendar_router.get("", dependencies=[Depends(require_permission("calendar.read"))])
async def list_calendar(db: DB, user: Reader, kind: str | None = None):
    return await CampusService(db).calendar(user, kind)


@calendar_router.post("", status_code=201, dependencies=[Depends(require_permission("calendar.write"))])
async def create_calendar(body: CalendarIn, db: DB, user: Reader):
    return await CampusService(db).add_calendar(user, body)
