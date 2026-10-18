"""School-owned content library: notes, question banks, videos and links tagged by board, grade and subject."""

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

BOARDS = ("CBSE", "ICSE", "ISC", "STATE", "IB", "IGCSE", "NIOS", "OTHER")
KINDS = ("NOTE", "QUESTION_BANK", "VIDEO", "LINK")


class ContentItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_items"
    __table_args__ = (Index("ix_content_items_school_board", "school_id", "board"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # NOTE|QUESTION_BANK|VIDEO|LINK
    board: Mapped[str] = mapped_column(String(10), nullable=False)  # CBSE|ICSE|ISC|STATE|IB|IGCSE|NIOS|OTHER
    grade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grades.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
