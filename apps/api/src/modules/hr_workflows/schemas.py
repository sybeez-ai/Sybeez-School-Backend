from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

WorkflowType = Literal["ONBOARDING", "OFFBOARDING"]
WorkflowStatus = Literal["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"]


def _strip(v):
    return v.strip() if isinstance(v, str) else v


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    assignee_user_id: UUID | None = None

    _trim = field_validator("title", "description", mode="before")(_strip)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    assignee_user_id: UUID | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)

    _trim = field_validator("title", "description", mode="before")(_strip)


class TaskToggle(BaseModel):
    done: bool


class WorkflowCreate(BaseModel):
    employee_id: UUID
    workflow_type: WorkflowType
    start_date: date
    expected_completion_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    owner_user_id: UUID | None = None
    tasks: list[TaskIn] | None = Field(default=None, max_length=50)

    _trim = field_validator("notes", mode="before")(_strip)

    @model_validator(mode="after")
    def _dates(self):
        if (
            self.expected_completion_date
            and self.expected_completion_date < self.start_date
        ):
            raise ValueError("Expected completion date cannot be before the start date")
        return self


class WorkflowUpdate(BaseModel):
    start_date: date | None = None
    expected_completion_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    owner_user_id: UUID | None = None

    _trim = field_validator("notes", mode="before")(_strip)


class WorkflowCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    _trim = field_validator("reason", mode="before")(_strip)


class OwnerOut(BaseModel):
    user_id: UUID
    full_name: str


class TemplateOut(BaseModel):
    workflow_type: WorkflowType
    tasks: list[str]


class TaskOut(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    sort_order: int
    is_done: bool
    completed_at: datetime | None = None
    completed_by_name: str | None = None
    due_date: date | None = None
    is_overdue: bool = False
    assignee_user_id: UUID | None = None
    assignee_name: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowOut(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: str
    employee_code: str | None = None
    department_name: str | None = None
    designation_name: str | None = None
    workflow_type: WorkflowType
    status: WorkflowStatus
    start_date: date
    expected_completion_date: date | None = None
    completed_at: datetime | None = None
    completed_by_name: str | None = None
    cancelled_at: datetime | None = None
    cancelled_by_name: str | None = None
    cancel_reason: str | None = None
    notes: str | None = None
    owner_user_id: UUID | None = None
    owner_name: str | None = None
    created_by_name: str | None = None
    updated_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    total_tasks: int
    done_tasks: int
    overdue_tasks: int
    tasks: list[TaskOut] | None = None
