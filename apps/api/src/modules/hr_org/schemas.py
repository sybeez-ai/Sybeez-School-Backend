from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _strip(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


class _NameFields(BaseModel):
    @field_validator("name", mode="before", check_fields=False)
    @classmethod
    def _trim_name(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("description", "code", mode="before", check_fields=False)
    @classmethod
    def _trim_optional(cls, v):
        return _strip(v) if isinstance(v, str) else v


class DepartmentCreate(_NameFields):
    name: str = Field(min_length=2, max_length=120)
    code: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=2000)
    head_employee_id: UUID | None = None


class DepartmentUpdate(_NameFields):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    code: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=2000)
    head_employee_id: UUID | None = None
    is_active: bool | None = None


class DepartmentOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    code: str | None
    description: str | None
    head_employee_id: UUID | None
    head_name: str | None
    is_active: bool
    employee_count: int
    created_at: datetime
    updated_at: datetime


class DesignationCreate(_NameFields):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class DesignationUpdate(_NameFields):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class DesignationOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    description: str | None
    is_active: bool
    employee_count: int
    created_at: datetime
    updated_at: datetime
