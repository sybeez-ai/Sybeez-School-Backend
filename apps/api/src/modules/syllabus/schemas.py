from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SyllabusCreate(BaseModel):
    academic_year_id: UUID
    grade_id: UUID
    subject_id: UUID
    title: str = Field(min_length=2, max_length=255)
    description: str | None = None


class SyllabusTopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    order_index: int | None = None
    parent_topic_id: UUID | None = None


class SyllabusTopicOut(BaseModel):
    id: UUID
    syllabus_id: UUID
    parent_topic_id: UUID | None = None
    name: str
    description: str | None = None
    order_index: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SyllabusOut(BaseModel):
    id: UUID
    school_id: UUID
    academic_year_id: UUID
    grade_id: UUID
    subject_id: UUID
    title: str
    description: str | None = None
    created_at: datetime
    academic_year_name: str | None = None
    grade_name: str | None = None
    subject_name: str | None = None
    subject_code: str | None = None
    topic_count: int = 0
    topics: list[SyllabusTopicOut] = []

    model_config = {"from_attributes": True}
