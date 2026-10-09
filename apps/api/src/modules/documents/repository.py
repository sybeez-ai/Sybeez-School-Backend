"""Persistence for documents — always filter by school_id for tenant isolation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Document


class DocumentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, school_id: UUID, doc_id: UUID) -> Document | None:
        return (
            await self.db.execute(
                select(Document).where(
                    Document.id == doc_id,
                    Document.school_id == school_id,
                )
            )
        ).scalar_one_or_none()

    async def list(
        self,
        *,
        school_id: UUID,
        category: str | None = None,
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
        q: str | None = None,
        active_only: bool = True,
        visibility_values: list[str] | None = None,
    ) -> list[Document]:
        stmt = select(Document).where(Document.school_id == school_id)
        if active_only:
            stmt = stmt.where(Document.is_active.is_(True))
        if category:
            stmt = stmt.where(Document.category == category)
        if class_id is not None:
            stmt = stmt.where(Document.class_id == class_id)
        if subject_id is not None:
            stmt = stmt.where(Document.subject_id == subject_id)
        if q:
            stmt = stmt.where(Document.title.ilike(f"%{q}%"))
        if visibility_values:
            stmt = stmt.where(Document.visibility.in_(visibility_values))
        stmt = stmt.order_by(Document.created_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_any_visibility(
        self,
        *,
        school_id: UUID,
        category: str | None = None,
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
        q: str | None = None,
    ) -> list[Document]:
        """No visibility filter — for admins."""
        return await self.list(
            school_id=school_id,
            category=category,
            class_id=class_id,
            subject_id=subject_id,
            q=q,
            active_only=True,
            visibility_values=None,
        )

    async def create(self, doc: Document) -> Document:
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def soft_delete(self, doc: Document) -> None:
        doc.is_active = False
        await self.db.flush()
