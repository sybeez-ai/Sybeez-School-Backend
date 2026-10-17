from uuid import UUID

from pydantic import BaseModel


class SchoolProfile(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    timezone: str
    logo_url: str | None = None

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
