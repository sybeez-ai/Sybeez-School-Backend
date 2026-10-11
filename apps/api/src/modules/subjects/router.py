from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.subjects.schemas import SubjectCreate, SubjectOut
from src.modules.subjects.service import SubjectService

router = APIRouter(prefix="/subjects", tags=["subjects"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[SubjectOut])
async def list_subjects(db: Annotated[AsyncSession, Depends(get_db)], user: Annotated[CurrentUser, Depends(require_permission("subjects.read"))]):
    return await SubjectService(db).list(_sid(user))


@router.post("", response_model=SubjectOut, status_code=201)
async def create_subject(
    body: SubjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("subjects.write"))],
):
    return await SubjectService(db).create(_sid(user), body)
