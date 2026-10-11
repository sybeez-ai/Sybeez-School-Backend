from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20000)
    class_id: UUID | None = None
    is_published: bool = True


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    class_id: UUID | None = None
    is_published: bool | None = None


class AnnouncementOut(BaseModel):
    id: UUID
    school_id: UUID
    author_id: UUID | None
    class_id: UUID | None
    title: str
    body: str
    is_published: bool
    status: str
    published_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
