from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict

DiaryMood = Literal["great", "good", "okay", "tired", "stressed"]


class DiaryEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entry_date: date
    title: str
    body: str
    mood: DiaryMood
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime


class DiaryEntryCreate(BaseModel):
    entry_date: date | None = None
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)
    mood: DiaryMood = "good"
    tags: list[str] = Field(default_factory=list, max_length=20)


class DiaryEntryUpdate(BaseModel):
    entry_date: date | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=20_000)
    mood: DiaryMood | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
