from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import Enrollment, Student, User
from src.modules.schools.provisioning import create_school_user
from src.modules.students.schemas import StudentCreate, StudentOut
from src.shared.enums import UserRole


class StudentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: StudentCreate) -> StudentOut:
        user, _ = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=UserRole.STUDENT,
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

    async def list(self, school_id: UUID) -> list[StudentOut]:
        result = await self.db.execute(select(Student).where(Student.school_id == school_id).order_by(Student.created_at.desc()))
        students = list(result.scalars().all())
        out = []
        for s in students:
            user = (await self.db.execute(select(User).where(User.id == s.user_id))).scalar_one()
            out.append(
                StudentOut(
                    id=s.id,
                    school_id=s.school_id,
                    user_id=s.user_id,
                    full_name=user.full_name,
                    email=user.email,
                    admission_no=s.admission_no,
                    date_of_birth=s.date_of_birth,
                    created_at=s.created_at,
                )
            )
        return out
