from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.enrollments.schemas import EnrollmentCreate, EnrollmentOut
from src.modules.enrollments.service import EnrollmentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/enrollments", tags=["enrollments"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[EnrollmentOut])
async def list_enrollments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    class_id: UUID | None = Query(None),
):
    return await EnrollmentService(db).list(_sid(user), class_id)


@router.post("", response_model=EnrollmentOut, status_code=201)
async def create_enrollment(
    body: EnrollmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await EnrollmentService(db).create(_sid(user), body)
