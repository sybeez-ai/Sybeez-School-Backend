from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AcademicYearCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    starts_on: date
    ends_on: date
    is_current: bool = False


class AcademicYearUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=64)
    starts_on: date | None = None
    ends_on: date | None = None
    is_current: bool | None = None


class AcademicYearOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    starts_on: date
    ends_on: date
    is_current: bool
    created_at: datetime

    model_config = {"from_attributes": True}
