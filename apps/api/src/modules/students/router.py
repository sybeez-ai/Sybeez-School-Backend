from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.students.schemas import StudentCreate, StudentOut, StudentUpdate
from src.modules.students.service import StudentService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/students", tags=["students"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[StudentOut])
async def list_students(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("students.read"))],
    page: PageQuery,
):
    items, total = await StudentService(db).list(user, _sid(user), page)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=StudentOut, status_code=201)
async def create_student(
    body: StudentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("students.write"))],
):
    return await StudentService(db).create(_sid(user), body, actor_id=user.id)


@router.get("/me", response_model=StudentOut)
async def my_student(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("students.read"))],
):
    return await StudentService(db).me(user, _sid(user))


@router.get("/{student_id}", response_model=StudentOut)
async def get_student(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("students.read"))],
):
    return await StudentService(db).get(user, _sid(user), student_id)


@router.patch("/{student_id}", response_model=StudentOut)
async def update_student(
    student_id: UUID,
    body: StudentUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("students.write"))],
):
    return await StudentService(db).update(user, _sid(user), student_id, body)
