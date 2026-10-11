from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ExamCreate(BaseModel):
    class_id: UUID
    subject_id: UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    scheduled_at: datetime | None = None
    max_score: float = Field(default=100, gt=0, le=10000)
    academic_year_id: UUID | None = None


class ExamUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    scheduled_at: datetime | None = None
    max_score: float | None = Field(default=None, gt=0, le=10000)


class ExamOut(BaseModel):
    id: UUID
    school_id: UUID
    class_id: UUID
    subject_id: UUID
    academic_year_id: UUID | None
    title: str
    description: str | None
    scheduled_at: datetime | None
    max_score: float
    created_at: datetime

    model_config = {"from_attributes": True}
