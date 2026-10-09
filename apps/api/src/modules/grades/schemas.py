from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GradeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    level: int = Field(1, ge=1, le=20)
    is_active: bool = True


class GradeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    level: int | None = Field(default=None, ge=1, le=20)
    is_active: bool | None = None


class GradeOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    level: int
    is_active: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}
