from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models import DiaryEntry, Student
from src.modules.diary.schemas import DiaryEntryCreate, DiaryEntryUpdate


class DiaryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _optional_student_id(self, school_id: UUID, user_id: UUID) -> UUID | None:
        st = (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()
        return st.id if st else None

    async def list_entries(self, school_id: UUID, user_id: UUID) -> list[DiaryEntry]:
        rows = (
            await self.db.execute(
                select(DiaryEntry)
                .where(
                    DiaryEntry.school_id == school_id,
                    DiaryEntry.is_deleted.is_(False),
                    or_(
                        DiaryEntry.user_id == user_id,
                        # Legacy rows before user_id backfill
                        DiaryEntry.student_id.in_(
                            select(Student.id).where(
                                Student.school_id == school_id, Student.user_id == user_id
                            )
                        ),
                    ),
                )
                .order_by(DiaryEntry.entry_date.desc(), DiaryEntry.created_at.desc())
            )
        ).scalars().all()
        return list(rows)

    async def create_entry(self, school_id: UUID, user_id: UUID, data: DiaryEntryCreate) -> DiaryEntry:
        student_id = await self._optional_student_id(school_id, user_id)
        entry = DiaryEntry(
            id=uuid4(),
            school_id=school_id,
            user_id=user_id,
            student_id=student_id,
            entry_date=data.entry_date or date.today(),
            title=data.title.strip(),
            body=data.body.strip(),
            mood=data.mood,
            tags=[t.strip() for t in (data.tags or []) if t and t.strip()],
            is_deleted=False,
        )
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def _owned_entry(self, school_id: UUID, user_id: UUID, entry_id: UUID) -> DiaryEntry:
        entry = await self.db.get(DiaryEntry, entry_id)
        if not entry or entry.school_id != school_id or entry.is_deleted:
            raise NotFoundError("Diary entry not found")
        if entry.user_id == user_id:
            return entry
        # Legacy student-owned row
        student_id = await self._optional_student_id(school_id, user_id)
        if student_id and entry.student_id == student_id:
            return entry
        raise NotFoundError("Diary entry not found")

    async def update_entry(
        self, school_id: UUID, user_id: UUID, entry_id: UUID, data: DiaryEntryUpdate
    ) -> DiaryEntry:
        entry = await self._owned_entry(school_id, user_id, entry_id)
        payload = data.model_dump(exclude_unset=True)
        if "title" in payload and payload["title"] is not None:
            entry.title = payload["title"].strip()
        if "body" in payload and payload["body"] is not None:
            entry.body = payload["body"].strip()
        if "mood" in payload and payload["mood"] is not None:
            entry.mood = payload["mood"]
        if "entry_date" in payload and payload["entry_date"] is not None:
            entry.entry_date = payload["entry_date"]
        if "tags" in payload and payload["tags"] is not None:
            entry.tags = [t.strip() for t in payload["tags"] if t and t.strip()]
        if entry.user_id is None:
            entry.user_id = user_id
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def delete_entry(self, school_id: UUID, user_id: UUID, entry_id: UUID) -> None:
        entry = await self._owned_entry(school_id, user_id, entry_id)
        entry.is_deleted = True
        await self.db.commit()
