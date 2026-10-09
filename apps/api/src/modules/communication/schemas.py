from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID | None = None
    title: str
    body: str
    category: str | None = None
    link_path: str | None = None
    entity_type: str | None = None
    entity_id: UUID | None = None
    priority: str = "NORMAL"
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    total: int
    page: int
    page_size: int
    unread_count: int


class UnreadCountOut(BaseModel):
    count: int


class AudienceIn(BaseModel):
    audience_type: str  # SCHOOL|ROLE|GRADE|SECTION|USER
    role: str | None = None
    grade_id: UUID | None = None
    section_id: UUID | None = None
    user_id: UUID | None = None


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    priority: str = "NORMAL"
    expires_at: datetime | None = None
    audiences: list[AudienceIn] = Field(default_factory=list)
    status: str = "DRAFT"


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = None
    priority: str | None = None
    expires_at: datetime | None = None
    audiences: list[AudienceIn] | None = None


class AudienceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    audience_type: str
    role: str | None = None
    grade_id: UUID | None = None
    section_id: UUID | None = None
    user_id: UUID | None = None


class AnnouncementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    title: str
    content: str
    status: str
    priority: str
    created_by: UUID | None = None
    published_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    audiences: list[AudienceOut] = []


class ConversationCreate(BaseModel):
    participant_user_id: UUID
    related_student_id: UUID | None = None
    subject: str | None = None
    initial_message: str = Field(min_length=1)


class ConversationOut(BaseModel):
    id: UUID
    school_id: UUID
    conversation_type: str
    subject: str | None = None
    related_student_id: UUID | None = None
    other_user_id: UUID | None = None
    other_user_name: str | None = None
    last_message: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    updated_at: datetime


class MessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    sender_user_id: UUID
    sender_name: str | None = None
    message: str
    message_type: str
    created_at: datetime
    deleted_at: datetime | None = None


class PreferenceItem(BaseModel):
    notification_type: str
    in_app_enabled: bool = True
    email_enabled: bool = False
    sms_enabled: bool = False
    whatsapp_enabled: bool = False


class PreferencesOut(BaseModel):
    items: list[PreferenceItem]


class PreferencesUpdate(BaseModel):
    items: list[PreferenceItem]


class CommunicationStatsOut(BaseModel):
    announcements_total: int
    announcements_active: int
    notifications_sent: int
    notifications_unread: int
    conversations: int


class MessageContactOut(BaseModel):
    user_id: UUID
    full_name: str
    role: str
    detail: str | None = None
    related_student_id: UUID | None = None


class NotificationDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID | None = None
    user_id: UUID | None = None
    channel: str
    notification_type: str | None = None
    to: str
    title: str | None = None
    body: str
    provider: str
    provider_message_id: str | None = None
    status: str
    attempts: int
    last_error: str | None = None
    created_at: datetime
    sent_at: datetime | None = None
