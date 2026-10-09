from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ProgressRowOut(BaseModel):
    """Raw StudentProgress row — one metric per subject."""

    id: UUID
    school_id: UUID
    student_id: UUID
    subject_id: UUID
    academic_year_id: UUID | None
    metric: str
    value: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubjectProgressOut(BaseModel):
    """Aggregated per-subject view combining all metrics."""

    subject_id: UUID
    subject_name: str | None
    homework_avg: float | None  # % of marks earned in graded homework
    exam_avg: float | None      # % earned in published exam marks
    overall: float | None       # mean of available metrics


class ProgressSummaryOut(BaseModel):
    """Full progress summary for one student."""

    student_id: UUID
    academic_year_id: UUID | None
    subjects: list[SubjectProgressOut]
    overall: float | None  # grand mean of all subject overalls


class RecomputeResult(BaseModel):
    """Response returned from POST /progress/recompute."""

    student_id: UUID
    academic_year_id: UUID | None
    subjects_processed: int
    summary: ProgressSummaryOut


class PracticeAttemptCreate(BaseModel):
    subject_id: UUID | None = None
    topic: str | None = None
    difficulty: str = "adaptive"
    questions_json: list | dict | str
    answers_json: list | dict | str
    score_pct: float | None = None


class PracticeAttemptOut(BaseModel):
    id: UUID
    student_user_id: UUID
    topic: str | None = None
    difficulty: str
    score_pct: float | None = None
    created_at: datetime
