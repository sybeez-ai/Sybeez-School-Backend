from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=32)
    description: str | None = None


class SubjectOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    code: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
