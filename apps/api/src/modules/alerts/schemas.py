from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID | None = None
    user_id: UUID | None = None
    channel: str
    notification_type: str | None = None
    event_key: str | None = None
    to: str
    to_masked: str = ""
    title: str | None = None
    body: str
    provider: str
    provider_message_id: str | None = None
    status: str
    attempts: int
    last_error: str | None = None
    error_code: str | None = None
    locale: str | None = None
    template_name: str | None = None
    scheduled_for: datetime | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    failed_at: datetime | None = None
    created_at: datetime


class DeliveryListOut(BaseModel):
    items: list[DeliveryOut]
    total: int


class AlertSettingsOut(BaseModel):
    school_id: UUID
    whatsapp_enabled: bool
    sms_enabled: bool
    quiet_hours_start: str
    quiet_hours_end: str
    timezone: str
    default_locale: str


class AlertSettingsUpdate(BaseModel):
    whatsapp_enabled: bool | None = None
    sms_enabled: bool | None = None
    quiet_hours_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone: str | None = None
    default_locale: str | None = Field(default=None, pattern=r"^[a-z]{2}$")


class TemplateOut(BaseModel):
    id: UUID
    school_id: UUID | None = None
    event_key: str
    locale: str
    whatsapp_template_name: str
    language_code: str
    variables: list[str]
    sms_dlt_template_id: str
    sms_text: str
    body_text: str
    category: str
    active: bool
    source: str


class TemplateUpdate(BaseModel):
    whatsapp_template_name: str | None = None
    language_code: str | None = None
    variables: list[str] | None = None
    sms_dlt_template_id: str | None = None
    sms_text: str | None = None
    body_text: str | None = None
    active: bool | None = None
    locale: str | None = None


class TemplateCreate(BaseModel):
    event_key: str = Field(min_length=1, max_length=64)
    locale: str = Field(pattern=r"^[a-z]{2}$")
    whatsapp_template_name: str = Field(min_length=1, max_length=128)
    language_code: str = Field(min_length=2, max_length=16)
    variables: list[str] = Field(default_factory=list)
    sms_dlt_template_id: str = ""
    sms_text: str
    body_text: str
    active: bool = True
