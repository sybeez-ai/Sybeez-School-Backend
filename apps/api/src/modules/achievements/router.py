"""Student achievements. Families read their own. Teachers and the school admin record them."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import Parent, Student, StudentParent
from src.db.models.communication import Achievement
from src.modules.access.deps import require_permission
from src.modules.fees.tenant import bind_tenant
from src.shared.enums import UserRole

router = APIRouter(prefix="/achievements", tags=["achievements"])
Reader = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT, UserRole.PARENT)),
]


class AchievementIn(BaseModel):
    student_id: UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    badge_code: str | None = Field(default=None, max_length=64)


async def _db(db: Annotated[AsyncSession, Depends(get_db)], user: Reader) -> AsyncSession:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id, user_id=user.id, role=user.role.value)
    return db


def _out(row: Achievement) -> dict:
    return {
        "id": str(row.id),
        "student_id": str(row.student_id),
        "title": row.title,
        "description": row.description,
        "badge_code": row.badge_code,
    }


async def _visible_students(db: AsyncSession, user: CurrentUser) -> set[UUID] | None:
    if user.role == UserRole.STUDENT:
        student = (
            await db.execute(select(Student).where(Student.school_id == user.school_id, Student.user_id == user.id))
        ).scalar_one_or_none()
        return {student.id} if student else set()
    if user.role == UserRole.PARENT:
        parent = (
            await db.execute(select(Parent).where(Parent.school_id == user.school_id, Parent.user_id == user.id))
        ).scalar_one_or_none()
        if parent is None:
            return set()
        rows = (await db.execute(select(StudentParent.student_id).where(StudentParent.parent_id == parent.id))).scalars().all()
        return set(rows)
    return None


@router.get("/health")
async def achievements_health(_: Annotated[CurrentUser, Depends(require_permission("achievements.read"))]):
    return {"module": "achievements", "status": "ok"}


@router.get("", dependencies=[Depends(require_permission("achievements.read"))])
async def list_achievements(
    db: Annotated[AsyncSession, Depends(_db)],
    user: Reader,
    student_id: UUID | None = None,
):
    stmt = select(Achievement).where(Achievement.school_id == user.school_id)
    mine = await _visible_students(db, user)
    if mine is not None:
        stmt = stmt.where(Achievement.student_id.in_(mine or {uuid4()}))
    elif student_id is not None:
        stmt = stmt.where(Achievement.student_id == student_id)
    rows = (await db.execute(stmt.order_by(Achievement.created_at.desc()).limit(200))).scalars().all()
    return [_out(row) for row in rows]


@router.post("", status_code=201, dependencies=[Depends(require_permission("achievements.write"))])
async def create_achievement(
    body: AchievementIn,
    db: Annotated[AsyncSession, Depends(_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    student = await db.get(Student, body.student_id)
    if student is None or student.school_id != user.school_id:
        raise NotFoundError("Student not found")
    row = Achievement(
        id=uuid4(),
        school_id=user.school_id,
        student_id=student.id,
        title=body.title.strip(),
        description=body.description,
        badge_code=(body.badge_code or "").strip() or None,
    )
    db.add(row)
    await db.flush()
    return _out(row)
