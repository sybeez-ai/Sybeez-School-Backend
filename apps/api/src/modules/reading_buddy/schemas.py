from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PassageOut(BaseModel):
    id: UUID
    class_level: int
    title: str
    body: str
    word_count: int
    source: str
    is_active: bool = True

    model_config = {"from_attributes": True}


class PassageCreate(BaseModel):
    class_level: int = Field(ge=1, le=5)
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=20, max_length=1200)


class PassageUpdate(BaseModel):
    is_active: bool


class ProgressOut(BaseModel):
    stars_total: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    sessions_completed: int = 0
    passages_completed: int = 0
    best_accuracy_pct: float | None = None
    last_read_on: date | None = None


class CapsOut(BaseModel):
    max_sessions_per_day: int
    max_session_minutes: int
    max_daily_minutes: int
    sessions_today: int
    sessions_left_today: int
    minutes_left_today: int
    retention_days: int
    retention_note: str


class MeOut(BaseModel):
    eligible: bool
    class_level: int | None
    progress: ProgressOut
    caps: CapsOut


class StartRequest(BaseModel):
    passage_id: UUID


class VoiceOut(BaseModel):
    value: str
    ws_url: str
    setup: dict
    max_minutes: int


class StartOut(BaseModel):
    session_id: UUID
    passage: PassageOut
    voice: VoiceOut


class CompleteRequest(BaseModel):
    transcript: str = Field(default="", max_length=6000)
    duration_seconds: int = Field(ge=0, le=3600)


class WordResult(BaseModel):
    word: str
    status: str  # correct | misread | skipped | not_reached


class SessionResult(BaseModel):
    id: UUID
    status: str
    stars: int
    words_total: int
    words_correct: int
    words_misread: int
    words_skipped: int
    accuracy_pct: float | None
    wcpm: float | None
    word_results: list[WordResult]
    message: str
    progress: ProgressOut


class SessionSummary(BaseModel):
    id: UUID
    passage_title: str
    started_at: datetime
    duration_seconds: int | None
    stars: int
    accuracy_pct: float | None
    words_total: int
    words_misread: int
    words_skipped: int


class StudentSummary(BaseModel):
    student_id: UUID
    progress: ProgressOut
    recent_sessions: list[SessionSummary]
    words_to_practise: list[str]
    note: str
