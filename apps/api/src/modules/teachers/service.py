from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Teacher, User
from src.modules.schools.provisioning import create_school_user
from src.modules.teachers.schemas import TeacherCreate, TeacherOut
from src.shared.enums import UserRole


class TeacherService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: TeacherCreate) -> TeacherOut:
        user, _ = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=UserRole.TEACHER,
        )
        teacher = Teacher(
            school_id=school_id,
            user_id=user.id,
            employee_code=data.employee_code,
            title=data.title,
        )
        self.db.add(teacher)
        await self.db.flush()
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

    async def list(self, school_id: UUID) -> list[TeacherOut]:
        result = await self.db.execute(select(Teacher).where(Teacher.school_id == school_id).order_by(Teacher.created_at.desc()))
        out = []
        for t in result.scalars().all():
            user = (await self.db.execute(select(User).where(User.id == t.user_id))).scalar_one()
            out.append(
                TeacherOut(
                    id=t.id,
                    school_id=t.school_id,
                    user_id=t.user_id,
                    full_name=user.full_name,
                    email=user.email,
                    employee_code=t.employee_code,
                    title=t.title,
                    created_at=t.created_at,
                )
            )
        return out
