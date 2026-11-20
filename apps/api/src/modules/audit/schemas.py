from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class AuditLogOut(BaseModel):
    id: UUID
    created_at: datetime
    school_id: UUID | None = None
    school_name: str | None = None
    actor_id: UUID | None = None
    actor_email: str | None = None
    action: str
    message: str | None = None
    details: dict | None = None

    model_config = {"from_attributes": True}
