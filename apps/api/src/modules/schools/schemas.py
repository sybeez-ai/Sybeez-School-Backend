from uuid import UUID

from pydantic import BaseModel, field_validator


class SchoolProfile(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    timezone: str
    logo_url: str | None = None

    @field_validator("logo_url", mode="before")
    @classmethod
    def _logo_path(cls, value):
        from src.core.storage import school_logo_path

        return school_logo_path(value)

    model_config = {"from_attributes": True}


class SchoolProfileUpdate(BaseModel):
    name: str | None = None


class BranchOut(BaseModel):
    """Payroll reads `name`. Finance branches also carry id and code for X-Branch-Id."""

    name: str
    id: UUID | None = None
    school_id: UUID | None = None
    code: str | None = None
    address: str | None = None
    active: bool | None = None


class BranchList(BaseModel):
    branches: list[BranchOut]
