from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import ClassRoom, Teacher, User
from src.modules.classes.schemas import ClassCreate, ClassOut, ClassUpdate


class ClassService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: ClassCreate) -> ClassOut:
        if data.class_teacher_id:
            await self._assert_teacher(school_id, data.class_teacher_id)
        row = ClassRoom(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return await self._to_out(row)

    async def list(self, school_id: UUID) -> list[ClassOut]:
        result = await self.db.execute(
            select(ClassRoom).where(ClassRoom.school_id == school_id).order_by(ClassRoom.name)
        )
        out: list[ClassOut] = []
        for row in result.scalars().all():
            out.append(await self._to_out(row))
        return out

    async def get(self, school_id: UUID, class_id: UUID) -> ClassRoom:
        row = (
            await self.db.execute(
                select(ClassRoom).where(ClassRoom.id == class_id, ClassRoom.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not row:
            raise NotFoundError("Section not found")
        return row

    async def update(self, school_id: UUID, class_id: UUID, data: ClassUpdate) -> ClassOut:
        row = await self.get(school_id, class_id)
        payload = data.model_dump(exclude_unset=True)

        if "class_teacher_id" in payload and payload["class_teacher_id"] is not None:
            await self._assert_teacher(school_id, payload["class_teacher_id"])

        if "section" in payload and payload["section"]:
            code = payload["section"].strip().upper()
            payload["section"] = code
            # Keep display name in sync: prefer "5-A" / "LKG-A" style
            if "name" not in payload:
                base = row.name.rsplit("-", 1)[0] if "-" in row.name else row.name
                payload["name"] = f"{base}-{code}"

        if "name" in payload and payload["name"]:
            name = payload["name"].strip()
            clash = (
                await self.db.execute(
                    select(ClassRoom).where(
                        ClassRoom.school_id == school_id,
                        ClassRoom.academic_year_id == row.academic_year_id,
                        ClassRoom.name == name,
                        ClassRoom.id != class_id,
                    )
                )
            ).scalar_one_or_none()
            if clash:
                raise ConflictError(f"Section '{name}' already exists in this year")
            payload["name"] = name

        for k, v in payload.items():
            setattr(row, k, v)
        await self.db.flush()
        return await self._to_out(row)

    async def _assert_teacher(self, school_id: UUID, teacher_id: UUID) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise NotFoundError("Teacher not found")
        return teacher

    async def _to_out(self, row: ClassRoom) -> ClassOut:
        teacher_name = None
        if row.class_teacher_id:
            teacher = await self.db.get(Teacher, row.class_teacher_id)
            if teacher:
                user = await self.db.get(User, teacher.user_id)
                teacher_name = user.full_name if user else None
        return ClassOut(
            id=row.id,
            school_id=row.school_id,
            name=row.name,
            grade_id=row.grade_id,
            academic_year_id=row.academic_year_id,
            section=row.section,
            capacity=row.capacity,
            class_teacher_id=row.class_teacher_id,
            class_teacher_name=teacher_name,
            created_at=row.created_at,
        )
