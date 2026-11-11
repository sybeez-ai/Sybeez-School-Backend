from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LinkCodeOut(BaseModel):
    code: str
    expires_in_seconds: int
    instructions: str


class UnlinkOut(BaseModel):
    removed: int


class AssistantSettingsOut(BaseModel):
    school_id: UUID
    assistant_enabled: bool
    leave_requests_enabled: bool


class AssistantSettingsUpdate(BaseModel):
    assistant_enabled: bool | None = None
    leave_requests_enabled: bool | None = None


class AssistantStatusOut(BaseModel):
    sending_enabled: bool
    sending_switch: str = "WHATSAPP_SENDING_ENABLED"
    provider_configured: bool
    phone_number_id_set: bool
    webhook_path: str
    school: AssistantSettingsOut | None = None
    usage: dict = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


class TranscriptItem(BaseModel):
    id: UUID
    school_id: UUID | None
    user_id: UUID | None
    direction: str
    body: str
    status: str
    persona: str | None
    send_reason: str | None
    phone_masked: str
    created_at: datetime


class TranscriptList(BaseModel):
    items: list[TranscriptItem]
    total: int


class InboundJob(BaseModel):
    body: str
