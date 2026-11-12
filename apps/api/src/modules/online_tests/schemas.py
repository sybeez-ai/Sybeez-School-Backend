"""Request and response models for online MCQ tests.

Student-facing models (StudentQuestionOut, AttemptStartOut, StudentResultOut) have no field that can
carry an answer key. Teacher models are separate so a key can never be serialised to a student by accident.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ResultRelease(StrEnum):
    MANUAL = "MANUAL"
    AFTER_CLOSE = "AFTER_CLOSE"


class OnlineTestSettings(BaseModel):
    shuffle_questions: bool = True
    shuffle_options: bool = True
    secure_mode: bool = True
    max_violations: int = Field(3, ge=0, le=10)  # 0 = log only, never auto-submit
    result_release: ResultRelease = ResultRelease.MANUAL
    show_answers: bool = True
    max_resumes: int = Field(3, ge=0, le=20)


class OptionIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Option text cannot be empty")
        return v


def _check_options(options: list[str], correct_index: int) -> None:
    lowered = [o.strip().lower() for o in options]
    if len(set(lowered)) != len(lowered):
        raise ValueError("Options must be different from each other")
    if not 0 <= correct_index < len(options):
        raise ValueError("Pick exactly one correct option")


class QuestionIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_index: int = Field(ge=0, le=5)
    explanation: str | None = Field(default=None, max_length=2000)
    points: int = Field(1, ge=1, le=100)
    difficulty: Literal["easy", "medium", "hard"] | None = None

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question text cannot be empty")
        return v

    @field_validator("options")
    @classmethod
    def _opts(cls, v: list[str]) -> list[str]:
        out = [o.strip() for o in v]
        if any(not o or len(o) > 300 for o in out):
            raise ValueError("Each option needs text (up to 300 characters)")
        return out

    @model_validator(mode="after")
    def _key(self):
        _check_options(self.options, self.correct_index)
        return self


class QuestionBulkItem(QuestionIn):
    source: Literal["MANUAL", "AI", "AI_EDITED"] = "MANUAL"
    reviewed: bool = False  # AI drafts must be explicitly confirmed by the teacher


class QuestionBulkIn(BaseModel):
    questions: list[QuestionBulkItem] = Field(min_length=1, max_length=50)
    ai_generation_id: UUID | None = None


class QuestionUpdate(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    options: list[str] | None = Field(default=None, min_length=2, max_length=6)
    correct_index: int | None = Field(default=None, ge=0, le=5)
    explanation: str | None = Field(default=None, max_length=2000)
    points: int | None = Field(default=None, ge=1, le=100)
    difficulty: Literal["easy", "medium", "hard"] | None = None


class ReorderIn(BaseModel):
    question_ids: list[UUID] = Field(min_length=1, max_length=200)


class OnlineTestCreate(BaseModel):
    class_id: UUID
    subject_id: UUID
    syllabus_topic_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    instructions: str | None = Field(default=None, max_length=5000)
    duration_minutes: int = Field(30, ge=1, le=300)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    settings: OnlineTestSettings = Field(default_factory=OnlineTestSettings)

    @model_validator(mode="after")
    def _window(self):
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            raise ValueError("The closing time must be after the opening time")
        return self


class OnlineTestUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    instructions: str | None = Field(default=None, max_length=5000)
    duration_minutes: int | None = Field(default=None, ge=1, le=300)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    settings: OnlineTestSettings | None = None


class OptionOut(BaseModel):
    id: str
    text: str


class StudentQuestionOut(BaseModel):
    id: UUID
    prompt: str
    options: list[OptionOut]
    points: int


class TeacherQuestionOut(BaseModel):
    id: UUID
    prompt: str
    options: list[OptionOut]
    correct_option_id: str | None = None
    correct_index: int | None = None
    explanation: str | None = None
    points: int
    sort_order: int
    difficulty: str | None = None
    source: str
    ai_reviewed: bool


class OnlineTestOut(BaseModel):
    id: UUID
    school_id: UUID
    class_id: UUID
    class_name: str | None = None
    subject_id: UUID
    subject_name: str | None = None
    teacher_id: UUID | None = None
    teacher_name: str | None = None
    title: str
    instructions: str | None = None
    status: str
    effective_status: str
    duration_minutes: int
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    total_points: int
    question_count: int = 0
    settings: OnlineTestSettings
    published_at: datetime | None = None
    results_released_at: datetime | None = None
    attempts_total: int = 0
    attempts_submitted: int = 0
    attempts_flagged: int = 0
    created_at: datetime


class OnlineTestDetailOut(OnlineTestOut):
    questions: list[TeacherQuestionOut] = []
    has_attempts: bool = False


class StudentTestOut(BaseModel):
    id: UUID
    title: str
    subject_name: str | None = None
    class_name: str | None = None
    instructions: str | None = None
    duration_minutes: int
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    question_count: int
    total_points: int
    effective_status: str  # UPCOMING | LIVE | CLOSED
    attempt_status: str | None = None  # None | IN_PROGRESS | SUBMITTED | AUTO_SUBMITTED | VOID
    attempt_id: UUID | None = None
    results_available: bool = False
    score: float | None = None
    max_score: float | None = None
    percentage: float | None = None
    grade_label: str | None = None
    secure_mode: bool = True
    max_violations: int = 3


class AttemptStartIn(BaseModel):
    client_info: dict | None = None
    fullscreen_supported: bool | None = None


class AnswerSavedOut(BaseModel):
    question_id: UUID
    selected_option_id: str | None
    server_now: datetime
    deadline_at: datetime


class AttemptStartOut(BaseModel):
    attempt_id: UUID
    test_id: UUID
    title: str
    instructions: str | None = None
    status: str
    resumed: bool
    started_at: datetime
    deadline_at: datetime
    server_now: datetime
    duration_minutes: int
    secure_mode: bool
    max_violations: int
    violation_count: int
    questions: list[StudentQuestionOut]
    answers: dict[str, str | None]  # question_id -> selected option id
    flagged: list[UUID] = []


class AnswerIn(BaseModel):
    selected_option_id: str | None = Field(default=None, max_length=32)
    flagged: bool = False


EventType = Literal[
    "FULLSCREEN_EXIT",
    "TAB_HIDDEN",
    "WINDOW_BLUR",
    "COPY_ATTEMPT",
    "PASTE_ATTEMPT",
    "CONTEXT_MENU",
    "PRINT_ATTEMPT",
    "DEVTOOLS_HINT",
    "FULLSCREEN_UNSUPPORTED",
    "NETWORK_LOST",
    # Native app (mobile lockdown) events
    "APP_BACKGROUND",
    "SCREENSHOT_ATTEMPT",
    "SCREEN_RECORDING",
    "MULTI_WINDOW",
    "LOCKDOWN_BROKEN",
    "LOCKDOWN_UNAVAILABLE",
]


class EventIn(BaseModel):
    type: EventType
    client_at: datetime | None = None
    meta: dict | None = None

    @field_validator("meta")
    @classmethod
    def _small(cls, v: dict | None) -> dict | None:
        if v is not None and len(str(v)) > 500:
            raise ValueError("meta too large")
        return v


class EventsIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=50)


class AttemptStateOut(BaseModel):
    attempt_id: UUID
    status: str
    server_now: datetime
    deadline_at: datetime
    seconds_left: int
    violation_count: int
    max_violations: int
    warnings_left: int | None = None
    auto_submitted: bool = False
    submit_reason: str | None = None


class SubmitOut(BaseModel):
    attempt_id: UUID
    status: str
    submit_reason: str | None = None
    results_pending: bool = True
    held_for_review: bool = False
    message: str


class ReviewedQuestionOut(BaseModel):
    id: UUID
    prompt: str
    options: list[OptionOut]
    selected_option_id: str | None = None
    correct_option_id: str | None = None
    is_correct: bool | None = None
    explanation: str | None = None
    points: int
    points_earned: float | None = None


class StudentResultOut(BaseModel):
    attempt_id: UUID
    test_id: UUID
    title: str
    released: bool
    held_for_review: bool = False
    status: str
    score: float | None = None
    max_score: float | None = None
    percentage: float | None = None
    grade_label: str | None = None
    submitted_at: datetime | None = None
    questions: list[ReviewedQuestionOut] = []


class ChildTestOut(BaseModel):
    test_id: UUID
    title: str
    subject_name: str | None = None
    class_name: str | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    effective_status: str
    attempt_status: str | None = None
    released: bool = False
    score: float | None = None
    max_score: float | None = None
    percentage: float | None = None
    grade_label: str | None = None


class ChildBriefOut(BaseModel):
    student_id: UUID
    name: str | None = None
    class_name: str | None = None


class AttemptRowOut(BaseModel):
    attempt_id: UUID | None = None
    student_id: UUID
    student_name: str | None = None
    roll_number: str | None = None
    status: str  # NOT_STARTED when no attempt
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    submit_reason: str | None = None
    violation_count: int = 0
    resume_count: int = 0
    score: float | None = None
    max_score: float | None = None
    percentage: float | None = None
    grade_label: str | None = None
    review_status: str = "NONE"
    review_note: str | None = None
    last_seen_at: datetime | None = None


class EventOut(BaseModel):
    id: UUID
    type: str
    counted: bool
    client_at: datetime | None = None
    server_at: datetime
    meta: dict | None = None


class AttemptDetailOut(AttemptRowOut):
    events: list[EventOut] = []
    answers: list[ReviewedQuestionOut] = []


class ReviewIn(BaseModel):
    decision: Literal["ACCEPT", "REOPEN", "VOID"]
    note: str | None = Field(default=None, max_length=1000)
    extra_minutes: int | None = Field(default=None, ge=1, le=300)


class QuestionStat(BaseModel):
    question_id: UUID
    prompt: str
    answered: int
    correct: int
    percent_correct: float | None = None
    option_counts: dict[str, int] = {}


class AnalyticsOut(BaseModel):
    test_id: UUID
    enrolled: int
    started: int
    submitted: int
    held_for_review: int
    average_percentage: float | None = None
    median_percentage: float | None = None
    highest: float | None = None
    lowest: float | None = None
    average_minutes: float | None = None
    distribution: list[int] = []  # ten buckets, 0-9 ... 90-100
    violations_total: int = 0
    questions: list[QuestionStat] = []


class AiGenerateIn(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "mixed"
    count: int = Field(5, ge=1, le=30)
    options_per_question: int = Field(4, ge=3, le=5)
    language: str = Field("English", min_length=2, max_length=40)
    source_notes: str | None = Field(default=None, max_length=6000)
    verify: bool = False


class AiDraftOut(BaseModel):
    prompt: str
    options: list[str]
    correct_index: int
    explanation: str = ""
    difficulty: str = "medium"
    topic_tag: str | None = None
    needs_review: bool = False
    review_reason: str | None = None


class AiGenerateOut(BaseModel):
    ai_generation_id: UUID
    drafts: list[AiDraftOut]
    dropped_invalid: int = 0
    dropped_duplicates: int = 0
    used_this_month: int
    monthly_cap: int


class SchoolSettingsOut(BaseModel):
    enabled: bool
    ai_monthly_cap: int
    ai_used_this_month: int = 0
    default_max_violations: int


class SchoolSettingsIn(BaseModel):
    enabled: bool | None = None
    ai_monthly_cap: int | None = Field(default=None, ge=0, le=100000)
    default_max_violations: int | None = Field(default=None, ge=0, le=10)


class ReleaseOut(BaseModel):
    test_id: UUID
    released_at: datetime
    notified: int
