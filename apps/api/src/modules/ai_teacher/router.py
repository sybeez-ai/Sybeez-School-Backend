"""Teacher read views for board pictures and AI memory."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.ai_teacher.memory import AiTeacherMemory
from src.modules.fees.tenant import bind_tenant
from src.shared.enums import UserRole

router = APIRouter(prefix="/ai-teacher", tags=["ai-teacher"])
Teacher = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))]


async def _db(db: Annotated[AsyncSession, Depends(get_db)], user: Teacher) -> AsyncSession:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id, user_id=user.id, role=user.role.value)
    return db


@router.get("/board-pictures")
async def list_pictures(
    class_id: UUID,
    db: Annotated[AsyncSession, Depends(_db)],
    user: Teacher,
    student_id: UUID | None = None,
):
    return await AiTeacherMemory(db).list_pictures(user, class_id=class_id, student_id=student_id)


@router.get("/board-pictures/{picture_id}")
async def download_picture(picture_id: UUID, db: Annotated[AsyncSession, Depends(_db)], user: Teacher):
    data, mime = await AiTeacherMemory(db).read_picture(user, picture_id)
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, no-store"})


@router.get("/memory/students/{student_id}")
async def student_memory(student_id: UUID, db: Annotated[AsyncSession, Depends(_db)], user: Teacher):
    return await AiTeacherMemory(db).student_facts(user, student_id)


@router.get("/memory/classes/{class_id}")
async def class_memory(class_id: UUID, db: Annotated[AsyncSession, Depends(_db)], user: Teacher):
    return await AiTeacherMemory(db).class_facts(user, class_id)


@router.get("/memory/sessions")
async def session_memory(class_id: UUID, db: Annotated[AsyncSession, Depends(_db)], user: Teacher):
    return await AiTeacherMemory(db).sessions(user, class_id)
