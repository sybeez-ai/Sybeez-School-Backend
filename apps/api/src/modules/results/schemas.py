from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ResultCreate(BaseModel):
    exam_id: UUID
    student_id: UUID
    score: float = Field(ge=0)
    remarks: str | None = Field(default=None, max_length=5000)


class ResultUpdate(BaseModel):
    score: float | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=5000)


class ResultOut(BaseModel):
    id: UUID
    school_id: UUID
    exam_id: UUID
    student_id: UUID
    score: float
    percentage: float | None
    grade_label: str | None
    remarks: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
