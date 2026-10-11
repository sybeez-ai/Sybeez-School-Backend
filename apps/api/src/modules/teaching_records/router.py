from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.teaching_records.schemas import TeachingRecordCreate, TeachingRecordOut
from src.modules.teaching_records.service import TeachingRecordService

router = APIRouter(prefix="/teaching-records", tags=["teaching_records"])


@router.post("", response_model=TeachingRecordOut, status_code=201)
async def create_record(
    body: TeachingRecordCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teaching_records.write"))],
):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return await TeachingRecordService(db).create(school_id=user.school_id, user_id=user.id, data=body)


@router.get("", response_model=list[TeachingRecordOut])
async def list_records(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teaching_records.read"))],
    class_id: UUID | None = Query(None),
):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    svc = TeachingRecordService(db)
    if class_id:
        return await svc.list_for_class(school_id=user.school_id, class_id=class_id)
    return await svc.list_mine(school_id=user.school_id)
