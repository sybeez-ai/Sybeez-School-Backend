from uuid import UUID

from pydantic import BaseModel


class SchoolProfile(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    timezone: str

    model_config = {"from_attributes": True}
