from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import ClassRoom, Enrollment, Parent, Student, StudentParent, User
from src.modules.parents.schemas import LinkParentRequest, ParentChildBrief, ParentCreate, ParentOut
from src.modules.schools.provisioning import create_school_user
from src.shared.enums import UserRole


class ParentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: ParentCreate) -> ParentOut:
        """Create or reuse parent for email — supports multiple children per parent."""
        user, _, temp = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=UserRole.PARENT,
        )
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user.id)
            )
        ).scalar_one_or_none()
        if parent is None:
            parent = Parent(school_id=school_id, user_id=user.id, phone=data.phone)
            self.db.add(parent)
            await self.db.flush()
        elif data.phone:
            parent.phone = data.phone
            await self.db.flush()

        if data.student_id:
            await self._link(
                school_id, data.student_id, parent.id, data.relationship, data.is_primary
            )

        out = await self._to_out(parent, user)
        out.temporary_password = temp
        return out

    async def list(self, school_id: UUID, *, q: str | None = None) -> list[ParentOut]:
        result = await self.db.execute(
            select(Parent).where(Parent.school_id == school_id).order_by(Parent.created_at.desc())
        )
        out: list[ParentOut] = []
        needle = (q or "").strip().lower()
        for p in result.scalars().all():
            user = (await self.db.execute(select(User).where(User.id == p.user_id))).scalar_one()
            item = await self._to_out(p, user)
            if needle:
                hay = " ".join(
                    filter(
                        None,
                        [
                            item.full_name,
                            item.email,
                            item.phone,
                            *[c.full_name for c in item.children],
                        ],
                    )
                ).lower()
                if needle not in hay:
                    continue
            out.append(item)
        return out

    async def link_to_student(
        self, school_id: UUID, student_id: UUID, data: LinkParentRequest
    ) -> StudentParent:
        return await self._link(
            school_id, student_id, data.parent_id, data.relationship, data.is_primary
        )

    async def unlink_from_student(
        self, school_id: UUID, student_id: UUID, parent_id: UUID
    ) -> None:
        link = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.student_id == student_id,
                    StudentParent.parent_id == parent_id,
                )
            )
        ).scalar_one_or_none()
        if not link:
            raise NotFoundError("Parent link not found")
        await self.db.delete(link)
        await self.db.flush()

    async def me(self, school_id: UUID, user_id: UUID) -> ParentOut:
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        user = (await self.db.execute(select(User).where(User.id == parent.user_id))).scalar_one()
        return await self._to_out(parent, user)

    async def my_children(self, school_id: UUID, user_id: UUID) -> list[ParentChildBrief]:
        me = await self.me(school_id, user_id)
        return me.children

    async def list_for_student(self, school_id: UUID, student_id: UUID) -> list[ParentOut]:
        result = await self.db.execute(
            select(StudentParent).where(
                StudentParent.school_id == school_id, StudentParent.student_id == student_id
            )
        )
        links = list(result.scalars().all())
        out: list[ParentOut] = []
        for link in links:
            parent = (
                await self.db.execute(select(Parent).where(Parent.id == link.parent_id))
            ).scalar_one()
            user = (await self.db.execute(select(User).where(User.id == parent.user_id))).scalar_one()
            out.append(await self._to_out(parent, user))
        return out

    async def _link(
        self,
        school_id: UUID,
        student_id: UUID,
        parent_id: UUID,
        relationship: str,
        is_primary: bool,
    ) -> StudentParent:
        student = (
            await self.db.execute(
                select(Student).where(Student.id == student_id, Student.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not student:
            raise NotFoundError("Student not found")
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.id == parent_id, Parent.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not parent:
            raise NotFoundError("Parent not found")

        if is_primary:
            await self.db.execute(
                update(StudentParent)
                .where(
                    StudentParent.student_id == student_id,
                    StudentParent.is_primary.is_(True),
                )
                .values(is_primary=False)
            )

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
            await self.db.flush()
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

    async def _to_out(self, parent: Parent, user: User) -> ParentOut:
        links = (
            await self.db.execute(select(StudentParent).where(StudentParent.parent_id == parent.id))
        ).scalars().all()
        children: list[ParentChildBrief] = []
        for link in links:
            student = (
                await self.db.execute(select(Student).where(Student.id == link.student_id))
            ).scalar_one_or_none()
            if not student:
                continue
            suser = (
                await self.db.execute(select(User).where(User.id == student.user_id))
            ).scalar_one()
            class_name = None
            enrollment = (
                await self.db.execute(
                    select(Enrollment)
                    .where(Enrollment.student_id == student.id, Enrollment.status == "ACTIVE")
                    .order_by(Enrollment.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if enrollment:
                classroom = await self.db.get(ClassRoom, enrollment.class_id)
                if classroom:
                    class_name = classroom.name
            children.append(
                ParentChildBrief(
                    student_id=student.id,
                    full_name=suser.full_name,
                    email=suser.email,
                    relationship=link.relation_type,
                    class_name=class_name,
                    admission_no=student.admission_no,
                )
            )

        return ParentOut(
            id=parent.id,
            school_id=parent.school_id,
            user_id=parent.user_id,
            full_name=user.full_name,
            email=user.email,
            phone=parent.phone,
            created_at=parent.created_at,
            children=children,
            is_active=bool(user.is_active),
        )
