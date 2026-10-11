from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.grades.schemas import GradeCreate, GradeOut
from src.modules.grades.service import GradeService

router = APIRouter(prefix="/grades", tags=["grades"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[GradeOut])
async def list_grades(db: Annotated[AsyncSession, Depends(get_db)], user: Annotated[CurrentUser, Depends(require_permission("grades.read"))]):
    return await GradeService(db).list(_sid(user))


@router.post("", response_model=GradeOut, status_code=201)
async def create_grade(
    body: GradeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("grades.write"))],
):
    return await GradeService(db).create(_sid(user), body)
