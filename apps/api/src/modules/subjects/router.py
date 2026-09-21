from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.subjects.schemas import SubjectCreate, SubjectOut
from src.modules.subjects.service import SubjectService
from src.shared.enums import UserRole

router = APIRouter(prefix="/subjects", tags=["subjects"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[SubjectOut])
async def list_subjects(db: Annotated[AsyncSession, Depends(get_db)], user: Annotated[CurrentUser, Depends(require_school_scope)]):
    return await SubjectService(db).list(_sid(user))


@router.post("", response_model=SubjectOut, status_code=201)
async def create_subject(
    body: SubjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await SubjectService(db).create(_sid(user), body)
