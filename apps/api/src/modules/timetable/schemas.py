from datetime import datetime, time
from uuid import UUID

from pydantic import BaseModel, Field


class TimetableEntryCreate(BaseModel):
    class_id: UUID
    subject_id: UUID
    teacher_id: UUID | None = None
    day_of_week: int = Field(ge=0, le=6)
    starts_at: time
    ends_at: time
    room: str | None = None


class TimetableEntryOut(BaseModel):
    id: UUID
    school_id: UUID
    class_id: UUID
    subject_id: UUID
    teacher_id: UUID | None
    day_of_week: int
    starts_at: time
    ends_at: time
    room: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
