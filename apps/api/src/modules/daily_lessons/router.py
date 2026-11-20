"""Teacher uploads what one section learned today. Other sections cannot read it."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.daily_lessons.service import DailyLessonService
from src.modules.fees.tenant import bind_tenant
from src.shared.enums import UserRole

router = APIRouter(prefix="/daily-lessons", tags=["daily-lessons"])
Reader = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT, UserRole.PARENT)),
]
Writer = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))]


class LessonIn(BaseModel):
    class_id: UUID
    subject_id: UUID
    taught_on: date
    chapter: str | None = Field(default=None, max_length=200)
    page_from: int | None = Field(default=None, ge=1, le=2000)
    page_to: int | None = Field(default=None, ge=1, le=2000)
    concept: str | None = Field(default=None, max_length=200)
    syllabus_topic_id: UUID | None = None
    taught_text: str = Field(min_length=1, max_length=20000)
    homework_text: str = Field(default="", max_length=20000)


async def _db(db: Annotated[AsyncSession, Depends(get_db)], user: Reader) -> AsyncSession:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id, user_id=user.id, role=user.role.value)
    return db


@router.post("", status_code=201)
async def save_lesson(body: LessonIn, db: Annotated[AsyncSession, Depends(_db)], user: Writer):
    return await DailyLessonService(db).create(user, body)


@router.get("")
async def list_lessons(
    db: Annotated[AsyncSession, Depends(_db)],
    user: Reader,
    class_id: UUID | None = None,
    taught_on: date | None = Query(None),
):
    return await DailyLessonService(db).list_for(user, class_id=class_id, taught_on=taught_on)


@router.get("/{lesson_id}")
async def get_lesson(lesson_id: UUID, db: Annotated[AsyncSession, Depends(_db)], user: Reader):
    return await DailyLessonService(db).get(user, lesson_id)


@router.post("/{lesson_id}/files", status_code=201)
async def upload_file(
    lesson_id: UUID,
    db: Annotated[AsyncSession, Depends(_db)],
    user: Writer,
    file: UploadFile = File(...),
):
    data = await file.read()
    return await DailyLessonService(db).add_file(user, lesson_id, file.filename or "page", data)


@router.get("/{lesson_id}/files/{file_id}")
async def download_file(lesson_id: UUID, file_id: UUID, db: Annotated[AsyncSession, Depends(_db)], user: Reader):
    data, mime, name = await DailyLessonService(db).read_file(user, lesson_id, file_id)
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"},
    )
