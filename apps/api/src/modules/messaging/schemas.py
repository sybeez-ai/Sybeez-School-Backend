from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    recipient_id: UUID
    subject: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=20000)


class MessageOut(BaseModel):
    id: UUID
    school_id: UUID
    sender_id: UUID
    recipient_id: UUID
    subject: str | None
    body: str
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
