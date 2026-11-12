from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TeachingRecordCreate(BaseModel):
    class_id: UUID
    subject_id: UUID
    taught_on: date
    topic: str = Field(min_length=1, max_length=255)
    summary: str | None = None
    objectives: str | None = None
    homework_notes: str | None = None
    syllabus_topic_id: UUID | None = None


class TeachingRecordOut(BaseModel):
    id: UUID
    school_id: UUID
    teacher_id: UUID
    class_id: UUID
    subject_id: UUID
    taught_on: date
    topic: str
    summary: str | None
    objectives: str | None
    homework_notes: str | None
    syllabus_topic_id: UUID | None = None
    created_at: datetime
    subject_name: str | None = None
    class_name: str | None = None
    teacher_name: str | None = None

    model_config = {"from_attributes": True}
