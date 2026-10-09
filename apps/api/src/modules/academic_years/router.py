from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.academic_years.schemas import AcademicYearCreate, AcademicYearOut, AcademicYearUpdate
from src.modules.academic_years.service import AcademicYearService
from src.shared.enums import UserRole

router = APIRouter(prefix="/academic-years", tags=["academic_years"])


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=list[AcademicYearOut])
async def list_years(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    return await AcademicYearService(db).list(_school(user))


@router.post("", response_model=AcademicYearOut, status_code=201)
async def create_year(
    body: AcademicYearCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AcademicYearService(db).create(_school(user), body)


@router.patch("/{year_id}", response_model=AcademicYearOut)
async def update_year(
    year_id: UUID,
    body: AcademicYearUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AcademicYearService(db).update(_school(user), year_id, body)
