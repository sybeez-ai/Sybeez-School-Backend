from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LearningSessionStart(BaseModel):
    subject_id: UUID | None = None
    teaching_record_id: UUID | None = None
    mode: str = Field(default="board", pattern="^(board|3d|picture)$")


class LearningSessionOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    subject_id: UUID | None
    teaching_record_id: UUID | None
    mode: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int

    model_config = {"from_attributes": True}
