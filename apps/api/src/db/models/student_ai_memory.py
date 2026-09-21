import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.constants import EMBEDDING_DIM
from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StudentAIMemory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """AI-derived memory — not official school fact."""

    __tablename__ = "student_ai_memory"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memory_type: Mapped[str] = mapped_column(String(64), default="preference", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
