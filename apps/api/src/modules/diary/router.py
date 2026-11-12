from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.diary.schemas import DiaryEntryCreate, DiaryEntryOut, DiaryEntryUpdate
from src.modules.diary.service import DiaryService
from src.shared.enums import UserRole
from src.modules.access.deps import public

router = APIRouter(prefix="/diary", tags=["diary"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
@public
async def diary_health():
    return {"module": "diary", "status": "ok"}


@router.get("/entries", response_model=list[DiaryEntryOut])
async def list_entries(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    return await DiaryService(db).list_entries(_sid(user), user.id)


@router.post("/entries", status_code=201, response_model=DiaryEntryOut)
async def create_entry(
    body: DiaryEntryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    return await DiaryService(db).create_entry(_sid(user), user.id, body)


@router.patch("/entries/{entry_id}", response_model=DiaryEntryOut)
async def update_entry(
    entry_id: UUID,
    body: DiaryEntryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    return await DiaryService(db).update_entry(_sid(user), user.id, entry_id, body)


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    await DiaryService(db).delete_entry(_sid(user), user.id, entry_id)
    return None
