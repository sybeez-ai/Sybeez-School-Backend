from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import NotFoundError
from src.db.models import Parent, Student, StudentParent, User
from src.modules.access.scope import restrict_students
from src.modules.parents.schemas import LinkParentRequest, ParentCreate, ParentOut
from src.modules.schools.provisioning import create_school_user
from src.modules.students.schemas import StudentOut
from src.shared.enums import UserRole
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


class ParentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: ParentCreate, actor_id: UUID | None = None) -> ParentOut:
        user, _ = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=UserRole.PARENT,
            actor_id=actor_id,
        )
        parent = Parent(school_id=school_id, user_id=user.id, phone=data.phone)
        self.db.add(parent)
        await self.db.flush()

        if data.student_id:
            await self._link(school_id, data.student_id, parent.id, data.relationship, data.is_primary)

        return ParentOut(
            id=parent.id,
            school_id=parent.school_id,
            user_id=parent.user_id,
            full_name=user.full_name,
            email=user.email,
            phone=parent.phone,
            created_at=parent.created_at,
        )

    async def list(self, school_id: UUID, page: PageParams) -> tuple[list[ParentOut], int]:
        stmt = select(Parent).where(Parent.school_id == school_id).order_by(Parent.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        out = []
        for p in result.scalars().all():
            user = (await self.db.execute(select(User).where(User.id == p.user_id))).scalar_one()
            out.append(
                ParentOut(
                    id=p.id,
                    school_id=p.school_id,
                    user_id=p.user_id,
                    full_name=user.full_name,
                    email=user.email,
                    phone=p.phone,
                    created_at=p.created_at,
                )
            )
        return out, total

    async def me(self, user: CurrentUser, school_id: UUID) -> ParentOut:
        parent = (
            await self.db.execute(select(Parent).where(Parent.user_id == user.id, Parent.school_id == school_id))
        ).scalar_one_or_none()
        if parent is None:
            raise NotFoundError("Parent profile not found")
        account = (await self.db.execute(select(User).where(User.id == parent.user_id))).scalar_one()
        return ParentOut(
            id=parent.id,
            school_id=parent.school_id,
            user_id=parent.user_id,
            full_name=account.full_name,
            email=account.email,
            phone=parent.phone,
            created_at=parent.created_at,
        )

    async def my_children(self, user: CurrentUser, school_id: UUID) -> list[StudentOut]:
        parent = (
            await self.db.execute(select(Parent).where(Parent.user_id == user.id, Parent.school_id == school_id))
        ).scalar_one_or_none()
        if parent is None:
            raise NotFoundError("Parent profile not found")
        stmt = restrict_students(select(Student).where(Student.school_id == school_id), user, school_id)
        students = list((await self.db.execute(stmt)).scalars().all())
        out = []
        for student in students:
            account = (await self.db.execute(select(User).where(User.id == student.user_id))).scalar_one()
            out.append(
                StudentOut(
                    id=student.id,
                    school_id=student.school_id,
                    user_id=student.user_id,
                    full_name=account.full_name,
                    email=account.email,
                    admission_no=student.admission_no,
                    date_of_birth=student.date_of_birth,
                    created_at=student.created_at,
                )
            )
        return out

    async def link_to_student(self, school_id: UUID, student_id: UUID, data: LinkParentRequest) -> StudentParent:
        return await self._link(school_id, student_id, data.parent_id, data.relationship, data.is_primary)

    async def list_for_student(self, school_id: UUID, student_id: UUID) -> list[ParentOut]:
        result = await self.db.execute(
            select(StudentParent).where(StudentParent.school_id == school_id, StudentParent.student_id == student_id)
        )
        links = list(result.scalars().all())
        out = []
        for link in links:
            parent = (await self.db.execute(select(Parent).where(Parent.id == link.parent_id))).scalar_one()
            user = (await self.db.execute(select(User).where(User.id == parent.user_id))).scalar_one()
            out.append(
                ParentOut(
                    id=parent.id,
                    school_id=parent.school_id,
                    user_id=parent.user_id,
                    full_name=user.full_name,
                    email=user.email,
                    phone=parent.phone,
                    created_at=parent.created_at,
                )
            )
        return out

    async def _link(
        self, school_id: UUID, student_id: UUID, parent_id: UUID, relationship: str, is_primary: bool
    ) -> StudentParent:
        student = (
            await self.db.execute(select(Student).where(Student.id == student_id, Student.school_id == school_id))
        ).scalar_one_or_none()
        if not student:
            raise NotFoundError("Student not found")
        parent = (
            await self.db.execute(select(Parent).where(Parent.id == parent_id, Parent.school_id == school_id))
        ).scalar_one_or_none()
        if not parent:
            raise NotFoundError("Parent not found")
        existing = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.student_id == student_id, StudentParent.parent_id == parent_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.relation_type = relationship
            existing.is_primary = is_primary
            return existing
        link = StudentParent(
            school_id=school_id,
            student_id=student_id,
            parent_id=parent_id,
            relation_type=relationship,
            is_primary=is_primary,
        )
        self.db.add(link)
        await self.db.flush()
        return link
