"""Pure readiness maths for exam prep (no database). Explainable on purpose: nothing here is invented.

Topic mastery = mean score of the last 5 attempts on that topic (practice quizzes and mock tests together), the same
rule as the learning path. A subject's readiness band comes from the average topic mastery and, when the school has a
syllabus for the subject, how many of its topics the student has attempted.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from src.modules.learning_paths.service import Attempt, build_path

READY_MASTERY, GETTING_READY_MASTERY, READY_COVERAGE = 80.0, 60.0, 60.0
METHOD = (
    "Topic mastery is the average of your last 5 scores on that topic (practice and mock tests). Ready means average "
    "mastery of 80% or more and, if your school has a syllabus for the subject, at least 60% of its topics attempted."
)


def band(mastery: float | None, coverage_pct: float | None) -> str:
    if mastery is None:
        return "not_started"
    if mastery >= READY_MASTERY and (coverage_pct is None or coverage_pct >= READY_COVERAGE):
        return "ready"
    if mastery >= GETTING_READY_MASTERY:
        return "getting_ready"
    return "needs_work"


def subject_readiness(
    subject_id: UUID | None,
    name: str | None,
    attempts: list[Attempt],
    syllabus_topics: list[str],
    mocks: list[tuple[datetime, float]],
    now: datetime,
) -> dict:
    path = build_path(attempts, {subject_id: name or ""} if subject_id else {}, now)
    topics = path["topics"]
    mastery = round(sum(t["mastery"] for t in topics) / len(topics), 1) if topics else None
    syl = {t.strip().lower() for t in syllabus_topics if t and t.strip()}
    covered = {t["topic"].strip().lower() for t in topics} & syl
    coverage_pct = round(100 * len(covered) / len(syl), 1) if syl else None
    recent = sorted(mocks, key=lambda m: m[0], reverse=True)[:5]
    return {
        "subject_id": str(subject_id) if subject_id else None,
        "subject_name": name,
        "band": band(mastery, coverage_pct),
        "mastery": mastery,
        "topics_attempted": len(topics),
        "syllabus_topics": len(syl) or None,
        "syllabus_covered": len(covered) if syl else None,
        "coverage_pct": coverage_pct,
        "weak_topics": [t for t in topics if t["status"] == "weak"][:8],
        "building_topics": [t for t in topics if t["status"] == "building"][:8],
        "topics": topics,
        "mock_count": len(mocks),
        "recent_mocks": [round(p, 1) for _, p in recent],
        "last_mock_pct": round(recent[0][1], 1) if recent else None,
    }
