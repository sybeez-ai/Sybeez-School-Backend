from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from src.modules.parent_messages.languages import LANGUAGES


def _lang(value: str) -> str:
    code = value.strip().lower()
    if code not in LANGUAGES:
        raise ValueError("Unsupported language")
    return code


class LanguageOption(BaseModel):
    code: str
    label: str


class PreferenceOut(BaseModel):
    preferred_language: str
    whatsapp_opt_in: bool
    whatsapp_consent_at: datetime | None = None
    has_phone: bool
    whatsapp_available: bool  # school switched WhatsApp on AND Sybeez has Meta credentials
    languages: list[LanguageOption]


class PreferenceUpdate(BaseModel):
    preferred_language: str | None = None
    whatsapp_opt_in: bool | None = None

    @field_validator("preferred_language")
    @classmethod
    def _check(cls, v: str | None) -> str | None:
        return None if v is None else _lang(v)


class InboxItem(BaseModel):
    source_type: str
    id: UUID
    title: str
    body: str
    original_title: str
    original_body: str
    language: str  # language of title/body as shown
    translated: bool
    translation_status: str | None = None  # AUTO | EDITED
    student_name: str | None = None
    kind: str | None = None
    created_at: datetime


class InboxOut(BaseModel):
    items: list[InboxItem]
    preferred_language: str


class TranslationOut(BaseModel):
    language: str
    label: str
    title: str
    body: str
    status: str
    needs_review: bool
    review_reason: str | None = None
    stale: bool = False
    edited_at: datetime | None = None


class TranslationSetOut(BaseModel):
    source_type: str
    source_id: UUID
    original_title: str
    original_body: str
    original_language: str
    translations: list[TranslationOut]
    audience_languages: dict[str, int]  # language code -> parents who chose it


class GenerateRequest(BaseModel):
    languages: list[str] | None = Field(default=None, max_length=10)

    @field_validator("languages")
    @classmethod
    def _check(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else [_lang(x) for x in v]


class TranslationEdit(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20000)


class ProgressUpdateCreate(BaseModel):
    student_id: UUID
    kind: str = Field(default="PROGRESS", pattern=r"^(PROGRESS|RESULT)$")
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    is_published: bool = True


class ProgressUpdateOut(BaseModel):
    id: UUID
    student_id: UUID
    kind: str
    title: str
    body: str
    is_published: bool
    published_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
