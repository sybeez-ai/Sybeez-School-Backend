from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.timetable.schemas import TimetableEntryCreate, TimetableEntryOut
from src.modules.timetable.service import TimetableService
from src.shared.enums import UserRole

router = APIRouter(prefix="/timetable", tags=["timetable"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[TimetableEntryOut])
async def list_timetable(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
    class_id: UUID | None = Query(None),
):
    return await TimetableService(db).list(_sid(user), class_id)


@router.post("", response_model=TimetableEntryOut, status_code=201)
async def create_entry(
    body: TimetableEntryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).create(_sid(user), body)
