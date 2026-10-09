from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.teaching_records.schemas import TeachingRecordCreate, TeachingRecordOut
from src.modules.teaching_records.service import TeachingRecordService
from src.shared.enums import UserRole

router = APIRouter(prefix="/teaching-records", tags=["teaching_records"])


@router.post("", response_model=TeachingRecordOut, status_code=201)
async def create_record(
    body: TeachingRecordCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER, UserRole.SCHOOL_ADMIN))],
):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return await TeachingRecordService(db).create(
        school_id=user.school_id, user_id=user.id, data=body
    )


@router.get("", response_model=list[TeachingRecordOut])
async def list_records(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.TEACHER, UserRole.SCHOOL_ADMIN, UserRole.STUDENT)),
    ],
    class_id: UUID | None = Query(None),
):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    svc = TeachingRecordService(db)
    if user.role == UserRole.STUDENT:
        if class_id:
            # Students may only see their own class
            allowed = await svc._student_class_id(user.school_id, user.id)
            if class_id != allowed:
                raise ForbiddenError("Not your class")
            return await svc.list_for_class(school_id=user.school_id, class_id=class_id)
        return await svc.list_for_student(school_id=user.school_id, user_id=user.id)
    if class_id:
        return await svc.list_for_class(school_id=user.school_id, class_id=class_id)
    if user.role == UserRole.SCHOOL_ADMIN:
        return await svc.list_all(school_id=user.school_id)
    return await svc.list_mine(school_id=user.school_id, user_id=user.id)
