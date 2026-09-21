from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    grade_id: UUID
    academic_year_id: UUID
    section: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=200)


class ClassOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    grade_id: UUID
    academic_year_id: UUID
    section: str | None
    capacity: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
