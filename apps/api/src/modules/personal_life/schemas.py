from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

GoalCategory = Literal["academic", "skills", "wellness", "personal"]
GoalStatus = Literal["active", "completed"]
HabitFrequency = Literal["daily", "weekdays"]


class MilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    completed: bool
    sort_order: int = 0


class MilestoneIn(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    completed: bool = False


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    category: GoalCategory
    target_date: date | None
    status: GoalStatus
    progress_percent: int
    completed_on: date | None
    milestones: list[MilestoneOut] = []
    created_at: datetime
    updated_at: datetime


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: GoalCategory = "personal"
    target_date: date | None = None
    milestones: list[MilestoneIn] = Field(default_factory=list, max_length=20)


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category: GoalCategory | None = None
    target_date: date | None = None
    status: GoalStatus | None = None
    milestones: list[MilestoneIn] | None = None


class HabitOut(BaseModel):
    id: UUID
    title: str
    description: str | None
    frequency: HabitFrequency
    weekly_target: int
    streak_days: int
    best_streak: int
    weekly_done: int
    checked_today: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class HabitCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    frequency: HabitFrequency = "daily"
    weekly_target: int = Field(default=7, ge=1, le=7)


class HabitUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    frequency: HabitFrequency | None = None
    weekly_target: int | None = Field(default=None, ge=1, le=7)
    is_active: bool | None = None


class HabitCheckinOut(BaseModel):
    habit_id: UUID
    checkin_date: date
    checked: bool
    streak_days: int
    weekly_done: int
