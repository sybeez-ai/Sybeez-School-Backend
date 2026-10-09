from uuid import UUID

from pydantic import BaseModel

from src.modules.progress.schemas import SubjectProgressOut


class FocusArea(BaseModel):
    """Subject where the student needs improvement (overall < threshold)."""

    subject_id: UUID
    subject_name: str | None
    overall: float | None
    practice_prompt: str


class Strength(BaseModel):
    """Subject where the student is performing well (overall >= threshold)."""

    subject_id: UUID
    subject_name: str | None
    overall: float | None


class IntelligenceReport(BaseModel):
    """
    Learning intelligence report for a student.
    Computed entirely from real DB data — no mock values.
    """

    student_id: UUID
    academic_year_id: UUID | None

    # Per-subject breakdown from StudentProgress rows
    subject_breakdown: list[SubjectProgressOut]

    # Subjects with overall < 50 %
    focus_areas: list[FocusArea]

    # Subjects with overall >= 75 %
    strengths: list[Strength]

    # One practice suggestion per focus area subject
    practice_prompts: list[str]

    # Homework: fraction of graded homework where student submitted (GRADED / total)
    recent_homework_rate: float | None  # 0.0 – 1.0

    # Average percentage across recent published/locked exam marks
    recent_exam_avg: float | None  # 0.0 – 100.0

    # Overall grand mean across all subjects
    overall: float | None


class CohortFocusTopic(BaseModel):
    subject_id: UUID
    subject: str
    topic: str
    avg_mastery: float
    students_needing_focus: int


class CohortAtRiskStudent(BaseModel):
    student_id: UUID
    student_name: str
    class_label: str | None
    overall_mastery: float
    top_focus: str | None
    focus_count: int


class CohortOverview(BaseModel):
    students_tracked: int
    avg_mastery: float | None
    at_risk_count: int
    focus_topics: int


class CohortIntelligenceReport(BaseModel):
    """School- or teacher-scoped cohort intelligence from live progress data."""

    scope: str  # school | teacher
    overview: CohortOverview
    top_focus_topics: list[CohortFocusTopic]
    at_risk_students: list[CohortAtRiskStudent]
