from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NoteColor = Literal["blue", "green", "amber", "violet", "rose", "slate"]


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    chapter: str
    title: str
    body: str
    tags: list[str] = []
    color: NoteColor
    pinned: bool
    created_at: datetime
    updated_at: datetime


class NoteCreate(BaseModel):
    subject: str = Field(default="", max_length=120)
    chapter: str = Field(default="", max_length=200)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=50_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    color: NoteColor = "blue"
    pinned: bool = False


class NoteUpdate(BaseModel):
    subject: str | None = Field(default=None, max_length=120)
    chapter: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=50_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    color: NoteColor | None = None
    pinned: bool | None = None
