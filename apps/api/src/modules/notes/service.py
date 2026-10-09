from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models.personal_note import PersonalNote
from src.modules.notes.schemas import NoteCreate, NoteUpdate


class NotesService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_notes(self, school_id: UUID, user_id: UUID) -> list[PersonalNote]:
        rows = (
            await self.db.execute(
                select(PersonalNote)
                .where(
                    PersonalNote.school_id == school_id,
                    PersonalNote.user_id == user_id,
                    PersonalNote.is_deleted.is_(False),
                )
                .order_by(PersonalNote.pinned.desc(), PersonalNote.updated_at.desc())
            )
        ).scalars().all()
        return list(rows)

    async def create_note(self, school_id: UUID, user_id: UUID, data: NoteCreate) -> PersonalNote:
        note = PersonalNote(
            id=uuid4(),
            school_id=school_id,
            user_id=user_id,
            subject=(data.subject or "").strip(),
            chapter=(data.chapter or "").strip(),
            title=data.title.strip(),
            body=data.body.strip(),
            tags=[t.strip() for t in (data.tags or []) if t and t.strip()][:20],
            color=data.color,
            pinned=bool(data.pinned),
            is_deleted=False,
        )
        self.db.add(note)
        await self.db.commit()
        await self.db.refresh(note)
        return note

    async def _owned(self, school_id: UUID, user_id: UUID, note_id: UUID) -> PersonalNote:
        note = await self.db.get(PersonalNote, note_id)
        if (
            not note
            or note.school_id != school_id
            or note.user_id != user_id
            or note.is_deleted
        ):
            raise NotFoundError("Note not found")
        return note

    async def update_note(
        self, school_id: UUID, user_id: UUID, note_id: UUID, data: NoteUpdate
    ) -> PersonalNote:
        note = await self._owned(school_id, user_id, note_id)
        payload = data.model_dump(exclude_unset=True)
        if "subject" in payload and payload["subject"] is not None:
            note.subject = payload["subject"].strip()
        if "chapter" in payload and payload["chapter"] is not None:
            note.chapter = payload["chapter"].strip()
        if "title" in payload and payload["title"] is not None:
            note.title = payload["title"].strip()
        if "body" in payload and payload["body"] is not None:
            note.body = payload["body"].strip()
        if "tags" in payload and payload["tags"] is not None:
            note.tags = [t.strip() for t in payload["tags"] if t and t.strip()][:20]
        if "color" in payload and payload["color"] is not None:
            note.color = payload["color"]
        if "pinned" in payload and payload["pinned"] is not None:
            note.pinned = bool(payload["pinned"])
        await self.db.commit()
        await self.db.refresh(note)
        return note

    async def delete_note(self, school_id: UUID, user_id: UUID, note_id: UUID) -> None:
        note = await self._owned(school_id, user_id, note_id)
        note.is_deleted = True
        await self.db.commit()
