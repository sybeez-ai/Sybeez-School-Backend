from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import Subject
from src.modules.subjects.schemas import SubjectCreate, SubjectOut, SubjectUpdate
from src.shared.enums import SubjectStatus


class SubjectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: SubjectCreate) -> SubjectOut:
        await self._assert_unique_code(school_id, data.code)
        row = Subject(
            school_id=school_id,
            name=data.name.strip(),
            code=data.code.strip().upper(),
            description=data.description,
            status=(data.status or SubjectStatus.ACTIVE.value).upper(),
        )
        self.db.add(row)
        await self.db.flush()
        return SubjectOut.model_validate(row)

    async def list(self, school_id: UUID, *, include_archived: bool = False) -> list[SubjectOut]:
        q = select(Subject).where(Subject.school_id == school_id)
        if not include_archived:
            q = q.where(Subject.status == SubjectStatus.ACTIVE.value)
        result = await self.db.execute(q.order_by(Subject.name))
        return [SubjectOut.model_validate(r) for r in result.scalars().all()]

    async def get(self, school_id: UUID, subject_id: UUID) -> SubjectOut:
        row = await self._get(school_id, subject_id)
        return SubjectOut.model_validate(row)

    async def update(self, school_id: UUID, subject_id: UUID, data: SubjectUpdate) -> SubjectOut:
        row = await self._get(school_id, subject_id)
        payload = data.model_dump(exclude_unset=True)
        if "code" in payload and payload["code"]:
            payload["code"] = payload["code"].strip().upper()
            await self._assert_unique_code(school_id, payload["code"], exclude_id=row.id)
        if "name" in payload and payload["name"]:
            payload["name"] = payload["name"].strip()
        if "status" in payload and payload["status"]:
            payload["status"] = payload["status"].upper()
        for k, v in payload.items():
            setattr(row, k, v)
        await self.db.flush()
        return SubjectOut.model_validate(row)

    async def archive(self, school_id: UUID, subject_id: UUID) -> SubjectOut:
        return await self.update(
            school_id, subject_id, SubjectUpdate(status=SubjectStatus.ARCHIVED.value)
        )

    async def _get(self, school_id: UUID, subject_id: UUID) -> Subject:
        row = (
            await self.db.execute(
                select(Subject).where(Subject.id == subject_id, Subject.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not row:
            raise NotFoundError("Subject not found")
        return row

    async def _assert_unique_code(
        self, school_id: UUID, code: str, exclude_id: UUID | None = None
    ) -> None:
        q = select(Subject).where(Subject.school_id == school_id, Subject.code == code.strip().upper())
        if exclude_id:
            q = q.where(Subject.id != exclude_id)
        if (await self.db.execute(q)).scalar_one_or_none():
            raise ConflictError("Subject code already exists")
