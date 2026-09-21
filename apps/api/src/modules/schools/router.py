from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_school_scope
from src.core.database import get_db
from src.modules.schools.schemas import SchoolProfile
from src.modules.schools.service import SchoolsService

router = APIRouter(prefix="/schools", tags=["schools"])


@router.get("/me", response_model=SchoolProfile)
async def my_school(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    return await SchoolsService(db).get_my_school(user.school_id)
