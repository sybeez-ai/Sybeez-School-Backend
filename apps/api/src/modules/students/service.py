from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import CurrentUser
from src.core.exceptions import NotFoundError, ValidationError
from src.db.models import Enrollment, Student, User
from src.modules.access.scope import restrict_students
from src.modules.schools.provisioning import create_school_user
from src.modules.students.schemas import StudentCreate, StudentOut, StudentUpdate
from src.shared.enums import PersonStatus, UserRole
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


class StudentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _out(self, student: Student, user: User) -> StudentOut:
        return StudentOut(
            id=student.id,
            school_id=student.school_id,
            user_id=student.user_id,
            full_name=user.full_name,
            email=user.email,
            admission_no=student.admission_no,
            date_of_birth=student.date_of_birth,
            created_at=student.created_at,
        )

    async def create(self, school_id: UUID, data: StudentCreate, actor_id: UUID | None = None) -> StudentOut:
        user, _ = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=UserRole.STUDENT,
            actor_id=actor_id,
        )
        student = Student(
            school_id=school_id,
            user_id=user.id,
            admission_no=data.admission_no,
            date_of_birth=data.date_of_birth,
        )
        self.db.add(student)
        await self.db.flush()

        if data.class_id and data.academic_year_id:
            self.db.add(
                Enrollment(
                    school_id=school_id,
                    student_id=student.id,
                    class_id=data.class_id,
                    academic_year_id=data.academic_year_id,
                    status="ACTIVE",
                )
            )
            await self.db.flush()

        return self._out(student, user)

    async def list(self, user: CurrentUser, school_id: UUID, page: PageParams) -> tuple[list[StudentOut], int]:
        stmt = restrict_students(
            select(Student).where(Student.school_id == school_id).order_by(Student.created_at.desc()),
            user,
            school_id,
        )
        result, total = await fetch_page(self.db, stmt, page)
        out = []
        for student in result.scalars().all():
            account = (await self.db.execute(select(User).where(User.id == student.user_id))).scalar_one()
            out.append(self._out(student, account))
        return out, total

    async def get(self, user: CurrentUser, school_id: UUID, student_id: UUID) -> StudentOut:
        stmt = restrict_students(
            select(Student).where(Student.id == student_id, Student.school_id == school_id),
            user,
            school_id,
        )
        student = (await self.db.execute(stmt)).scalar_one_or_none()
        if student is None:
            raise NotFoundError("Student not found")
        account = (await self.db.execute(select(User).where(User.id == student.user_id))).scalar_one()
        return self._out(student, account)

    async def me(self, user: CurrentUser, school_id: UUID) -> StudentOut:
        student = (
            await self.db.execute(select(Student).where(Student.user_id == user.id, Student.school_id == school_id))
        ).scalar_one_or_none()
        if student is None:
            raise NotFoundError("Student profile not found")
        account = (await self.db.execute(select(User).where(User.id == student.user_id))).scalar_one()
        return self._out(student, account)

    async def update(self, user: CurrentUser, school_id: UUID, student_id: UUID, data: StudentUpdate) -> StudentOut:
        stmt = restrict_students(
            select(Student).where(Student.id == student_id, Student.school_id == school_id),
            user,
            school_id,
        )
        student = (await self.db.execute(stmt)).scalar_one_or_none()
        if student is None:
            raise NotFoundError("Student not found")
        payload = data.model_dump(exclude_unset=True)
        if "status" in payload and payload["status"] is not None:
            allowed = {item.value for item in PersonStatus}
            if payload["status"] not in allowed:
                raise ValidationError("Invalid student status")
        for key, value in payload.items():
            setattr(student, key, value)
        await self.db.flush()
        account = (await self.db.execute(select(User).where(User.id == student.user_id))).scalar_one()
        return self._out(student, account)
