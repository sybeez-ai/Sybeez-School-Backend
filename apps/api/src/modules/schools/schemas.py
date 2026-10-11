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
    name: str


class BranchList(BaseModel):
    branches: list[BranchOut]
