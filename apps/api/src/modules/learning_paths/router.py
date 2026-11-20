from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.modules.learning_paths.service import path_for_user
from src.shared.enums import UserRole

router = APIRouter(prefix="/learning-paths", tags=["learning-paths"])


@router.get("/me")
async def my_path(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await path_for_user(db, user.id)
