from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import NotFoundError, ValidationError
from src.db.models import Teacher, User
from src.modules.schools.provisioning import create_school_user
from src.modules.teachers.schemas import TeacherCreate, TeacherOut, TeacherUpdate
from src.shared.enums import PersonStatus, UserRole
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


class TeacherService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _out(self, teacher: Teacher, user: User) -> TeacherOut:
        return TeacherOut(
            id=teacher.id,
            school_id=teacher.school_id,
            user_id=teacher.user_id,
            full_name=user.full_name,
            email=user.email,
            employee_code=teacher.employee_code,
            title=teacher.title,
            created_at=teacher.created_at,
        )

    async def create(self, school_id: UUID, data: TeacherCreate, actor_id: UUID | None = None) -> TeacherOut:
        user, _ = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=UserRole.TEACHER,
            actor_id=actor_id,
        )
        teacher = Teacher(
            school_id=school_id,
            user_id=user.id,
            employee_code=data.employee_code,
            title=data.title,
        )
        self.db.add(teacher)
        await self.db.flush()
        return self._out(teacher, user)

    async def list(self, school_id: UUID, page: PageParams) -> tuple[list[TeacherOut], int]:
        stmt = select(Teacher).where(Teacher.school_id == school_id).order_by(Teacher.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        out = []
        for teacher in result.scalars().all():
            account = (await self.db.execute(select(User).where(User.id == teacher.user_id))).scalar_one()
            out.append(self._out(teacher, account))
        return out, total

    async def get(self, school_id: UUID, teacher_id: UUID) -> TeacherOut:
        teacher = (
            await self.db.execute(select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id))
        ).scalar_one_or_none()
        if teacher is None:
            raise NotFoundError("Teacher not found")
        account = (await self.db.execute(select(User).where(User.id == teacher.user_id))).scalar_one()
        return self._out(teacher, account)

    async def me(self, user: CurrentUser, school_id: UUID) -> TeacherOut:
        teacher = (
            await self.db.execute(select(Teacher).where(Teacher.user_id == user.id, Teacher.school_id == school_id))
        ).scalar_one_or_none()
        if teacher is None:
            raise NotFoundError("Teacher profile not found")
        account = (await self.db.execute(select(User).where(User.id == teacher.user_id))).scalar_one()
        return self._out(teacher, account)

    async def update(self, school_id: UUID, teacher_id: UUID, data: TeacherUpdate) -> TeacherOut:
        teacher = (
            await self.db.execute(select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id))
        ).scalar_one_or_none()
        if teacher is None:
            raise NotFoundError("Teacher not found")
        payload = data.model_dump(exclude_unset=True)
        if "status" in payload and payload["status"] is not None:
            if payload["status"] not in {item.value for item in PersonStatus}:
                raise ValidationError("Invalid teacher status")
        for key, value in payload.items():
            setattr(teacher, key, value)
        await self.db.flush()
        account = (await self.db.execute(select(User).where(User.id == teacher.user_id))).scalar_one()
        return self._out(teacher, account)
