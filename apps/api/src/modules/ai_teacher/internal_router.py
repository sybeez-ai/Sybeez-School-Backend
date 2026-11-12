"""Service-to-service writes for the AI teacher. Callers send X-Internal-Token."""

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.modules.access.deps import service_auth
from src.modules.ai_teacher.memory import AiTeacherMemory, decode_image
from src.modules.fees.tenant import bind_tenant
from src.modules.internal.router import require_internal_token

router = APIRouter(prefix="/internal/v1/ai-teacher", tags=["ai-teacher-internal"])


class PictureIn(BaseModel):
    school_id: UUID
    student_id: UUID
    class_id: UUID
    taught_on: date
    topic: str = Field(min_length=1, max_length=200)
    content_base64: str
    lesson_id: UUID | None = None
    scene_spec: dict | None = None


class StudentFactIn(BaseModel):
    school_id: UUID
    student_id: UUID
    kind: str
    content: str = Field(min_length=1, max_length=4000)
    concept: str | None = Field(default=None, max_length=200)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)


class ClassFactIn(BaseModel):
    school_id: UUID
    class_id: UUID
    kind: str
    content: str = Field(min_length=1, max_length=4000)
    concept: str | None = None
    taught_on: date | None = None


class SessionIn(BaseModel):
    school_id: UUID
    session_id: UUID
    student_id: UUID
    class_id: UUID
    summary: str = ""
    facts: dict = Field(default_factory=dict)


async def _service_db(db: Annotated[AsyncSession, Depends(get_db)], school_id: UUID) -> AsyncSession:
    await bind_tenant(db, school_id, role="SERVICE")
    return db


@router.post("/board-pictures", status_code=201, dependencies=[Depends(require_internal_token)])
@service_auth
async def store_picture(body: PictureIn, db: Annotated[AsyncSession, Depends(get_db)]):
    await _service_db(db, body.school_id)
    return await AiTeacherMemory(db).store_picture(
        school_id=body.school_id,
        student_id=body.student_id,
        class_id=body.class_id,
        taught_on=body.taught_on,
        topic=body.topic,
        data=decode_image(body.content_base64),
        lesson_id=body.lesson_id,
        scene_spec=body.scene_spec,
    )


@router.post("/memory/student", status_code=201, dependencies=[Depends(require_internal_token)])
@service_auth
async def add_student_fact(body: StudentFactIn, db: Annotated[AsyncSession, Depends(get_db)]):
    await _service_db(db, body.school_id)
    return await AiTeacherMemory(db).add_student_fact(
        body.school_id, body.student_id, body.kind, body.content, body.concept, body.confidence
    )


@router.post("/memory/class", status_code=201, dependencies=[Depends(require_internal_token)])
@service_auth
async def add_class_fact(body: ClassFactIn, db: Annotated[AsyncSession, Depends(get_db)]):
    await _service_db(db, body.school_id)
    return await AiTeacherMemory(db).add_class_fact(
        body.school_id, body.class_id, body.kind, body.content, body.concept, body.taught_on
    )


@router.put("/memory/session", dependencies=[Depends(require_internal_token)])
@service_auth
async def save_session(body: SessionIn, db: Annotated[AsyncSession, Depends(get_db)]):
    await _service_db(db, body.school_id)
    return await AiTeacherMemory(db).save_session(
        body.school_id, body.session_id, body.student_id, body.class_id, body.summary, body.facts
    )


@router.get("/lessons", dependencies=[Depends(require_internal_token)])
@service_auth
async def lessons_for_service(
    school_id: UUID,
    class_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    taught_on: date | None = None,
):
    """What the teacher recorded for this section. Service auth, not a student token."""
    from sqlalchemy import select

    from src.db.models.daily_lesson import DailyLesson
    from src.modules.daily_lessons.service import DailyLessonService

    await _service_db(db, school_id)
    stmt = select(DailyLesson).where(DailyLesson.school_id == school_id, DailyLesson.class_id == class_id)
    if taught_on:
        stmt = stmt.where(DailyLesson.taught_on == taught_on)
    rows = (await db.execute(stmt.order_by(DailyLesson.taught_on.desc()).limit(30))).scalars().all()
    svc = DailyLessonService(db)
    return [svc._out(row, await svc._files(row.id)) for row in rows]
