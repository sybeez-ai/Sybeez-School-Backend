from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.reading_buddy import schemas as S
from src.modules.reading_buddy.service import ReadingBuddyService
from src.shared.enums import UserRole

router = APIRouter(prefix="/reading-buddy", tags=["reading-buddy"])

DB = Annotated[AsyncSession, Depends(get_db)]
Student = Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))]
Staff = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN, UserRole.TEACHER))]
Viewer = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN, UserRole.TEACHER, UserRole.PARENT)),
]
AnyReader = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.STUDENT, UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN, UserRole.TEACHER)),
]


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/me", response_model=S.MeOut)
async def me(db: DB, user: Student):
    return await ReadingBuddyService(db).me(user, _school(user))


@router.get("/passages", response_model=list[S.PassageOut])
async def passages(db: DB, user: AnyReader, class_level: int | None = None):
    return await ReadingBuddyService(db).list_passages(user, _school(user), class_level)


@router.post("/passages", response_model=S.PassageOut, status_code=201)
async def add_passage(body: S.PassageCreate, request: Request, db: DB, user: Staff):
    return await ReadingBuddyService(db).create_passage(user, _school(user), body, _ip(request))


@router.patch("/passages/{passage_id}", response_model=S.PassageOut)
async def toggle_passage(passage_id: UUID, body: S.PassageUpdate, db: DB, user: Staff):
    return await ReadingBuddyService(db).set_passage_active(user, _school(user), passage_id, body.is_active)


@router.post("/sessions", response_model=S.StartOut, status_code=201)
async def start_session(body: S.StartRequest, request: Request, db: DB, user: Student):
    return await ReadingBuddyService(db).start(user, _school(user), body.passage_id, _ip(request))


@router.post("/sessions/{session_id}/complete", response_model=S.SessionResult)
async def complete_session(session_id: UUID, body: S.CompleteRequest, request: Request, db: DB, user: Student):
    return await ReadingBuddyService(db).complete(user, _school(user), session_id, body, _ip(request))


@router.post("/sessions/{session_id}/abandon", status_code=204)
async def abandon_session(session_id: UUID, db: DB, user: Student):
    await ReadingBuddyService(db).abandon(user, _school(user), session_id)


@router.get("/students/{student_id}/summary", response_model=S.StudentSummary)
async def student_summary(student_id: UUID, db: DB, user: Viewer):
    return await ReadingBuddyService(db).student_summary(user, _school(user), student_id)
