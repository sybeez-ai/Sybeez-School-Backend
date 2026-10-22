"""Exam prep API: timed mock tests, weak-topic tracking and readiness. Students use it; parents see their child's."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.modules.exam_prep.service import ExamPrepService
from src.shared.enums import UserRole

router = APIRouter(prefix="/exam-prep", tags=["exam-prep"])
DB = Annotated[AsyncSession, Depends(get_db)]
Student = Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))]
Family = Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT, UserRole.SCHOOL_ADMIN))]


class StartIn(BaseModel):
    subject_id: UUID
    question_count: int = Field(default=10, ge=1, le=100)
    minutes: int | None = Field(default=None, ge=1, le=300)


class AnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=100)


@router.get("/subjects")
async def subjects(db: DB, user: Student):
    return await ExamPrepService(db).available_subjects(user)


@router.get("/readiness")
async def my_readiness(db: DB, user: Student):
    return await ExamPrepService(db).readiness(user)


@router.get("/mocks")
async def my_mocks(db: DB, user: Student):
    return await ExamPrepService(db).history(user)


@router.post("/mocks", status_code=201)
async def start_mock(body: StartIn, db: DB, user: Student):
    return await ExamPrepService(db).start_mock(user, body.subject_id, body.question_count, body.minutes)


@router.get("/mocks/{attempt_id}")
async def get_mock(attempt_id: UUID, db: DB, user: Student):
    return await ExamPrepService(db).get_mock(user, attempt_id)


@router.put("/mocks/{attempt_id}/answers")
async def save_answers(attempt_id: UUID, body: AnswersIn, db: DB, user: Student):
    return await ExamPrepService(db).save_answers(user, attempt_id, body.answers)


@router.post("/mocks/{attempt_id}/submit")
async def submit(attempt_id: UUID, body: AnswersIn, db: DB, user: Student):
    return await ExamPrepService(db).submit(user, attempt_id, body.answers)


@router.get("/children/{student_id}/readiness")
async def child_readiness(student_id: UUID, db: DB, user: Family):
    return await ExamPrepService(db).readiness(user, student_id)


@router.get("/children/{student_id}/mocks")
async def child_mocks(student_id: UUID, db: DB, user: Family):
    return await ExamPrepService(db).history(user, student_id)
