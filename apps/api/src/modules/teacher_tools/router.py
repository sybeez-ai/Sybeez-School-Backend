from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import AiGradeSuggestion, Homework, HomeworkSubmission
from src.modules.access.audit import write_audit, write_scoped_audit
from src.modules.homework.schemas import SubmissionGrade
from src.modules.homework.service import HomeworkService
from src.modules.teacher_tools import ai_client
from src.shared.enums import UserRole

router = APIRouter(prefix="/teacher-tools", tags=["teacher-tools"])
DB = Annotated[AsyncSession, Depends(get_db)]
Teacher = Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER, UserRole.SCHOOL_ADMIN))]


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


async def audit(db, user: CurrentUser, *, action: str, resource_type: str, resource_id: str | None, message: str,
                details: dict | None = None) -> None:
    """Teachers cannot write audit_logs directly; the session-scoped function does it for them."""
    try:
        async with db.begin_nested():
            await write_scoped_audit(db, action=action, resource_type=resource_type, resource_id=resource_id,
                                     message=message, details=details)
    except Exception:  # no session context (admin/service path): write as the actor
        await write_audit(db, school_id=_sid(user), actor_id=user.id, action=action, resource_type=resource_type,
                          resource_id=resource_id, message=message, details=details)


class DraftBody(BaseModel):
    kind: str = Field(pattern="^(lesson_plan|homework)$")
    topic: str = Field(min_length=2, max_length=200)
    subject_name: str | None = Field(default=None, max_length=120)
    class_label: str | None = Field(default=None, max_length=80)
    duration_minutes: int = Field(40, ge=10, le=180)
    language: str = Field("English", min_length=2, max_length=40)
    notes: str | None = Field(default=None, max_length=6000)


@router.post("/draft")
async def draft(body: DraftBody, db: DB, user: Teacher):
    """A draft for the teacher to edit. Nothing is saved or shared."""
    out = await ai_client.call(user, "/api/v1/ai/teacher-tools/draft", body.model_dump())
    await audit(
        db, user, action="teacher_tools.draft", resource_type="draft",
        resource_id=None, message=f"AI {body.kind} draft generated", details={"kind": body.kind, "topic": body.topic[:80]},
    )
    return out


class SuggestBody(BaseModel):
    submission_id: UUID
    rubric: str | None = Field(default=None, max_length=3000)


def _out(row: AiGradeSuggestion) -> dict:
    return {
        "id": str(row.id), "submission_id": str(row.submission_id), "suggested_marks": row.suggested_marks,
        "max_marks": row.max_marks, "reasoning": row.reasoning, "strengths": row.strengths, "improve": row.improve,
        "confidence": row.confidence, "status": row.status, "final_marks": row.final_marks,
        "note": "AI suggestion. Nothing is recorded until you approve it.",
    }


@router.post("/grading/suggest", status_code=201)
async def suggest(body: SuggestBody, db: DB, user: Teacher):
    school_id = _sid(user)
    svc = HomeworkService(db)
    sub = await svc.repo.get_submission_by_id(school_id=school_id, submission_id=body.submission_id)
    if not sub:
        raise NotFoundError("Submission not found")
    hw: Homework | None = await svc.repo.get(school_id, sub.homework_id)
    if not hw:
        raise NotFoundError("Homework not found")
    await svc._assert_can_manage(school_id=school_id, user_id=user.id, role=user.role.value, homework=hw)
    if not (sub.content or "").strip():
        raise ConflictError("This submission has no written answer to grade")
    max_marks = float(hw.max_marks or 10)
    ai = await ai_client.call(
        user,
        "/api/v1/ai/teacher-tools/grade",
        {"question": f"{hw.title}\n{hw.description or ''}".strip()[:2000], "rubric": body.rubric, "max_marks": max_marks,
         "answer": sub.content[:6000]},
    )
    row = AiGradeSuggestion(
        school_id=school_id, submission_id=sub.id, class_id=hw.class_id,
        suggested_marks=min(max(float(ai["suggested_marks"]), 0.0), max_marks), max_marks=max_marks,
        reasoning=str(ai["reasoning"])[:2000], strengths=list(ai.get("strengths") or [])[:6],
        improve=list(ai.get("improve") or [])[:6], confidence=str(ai.get("confidence") or "low")[:8],
        status="SUGGESTED", requested_by=user.id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db, user, action="grading.suggested", resource_type="ai_grade_suggestion",
        resource_id=str(row.id), message="AI grading suggestion created",
        details={"submission": str(sub.id), "suggested": row.suggested_marks, "max": max_marks},
    )
    return _out(row)


class ApproveBody(BaseModel):
    final_marks: float | None = Field(default=None, ge=0, le=1000)  # omit to accept the suggestion as is
    feedback: str | None = Field(default=None, max_length=5000)


async def _load(db, user: CurrentUser, suggestion_id: UUID) -> AiGradeSuggestion:
    row = await db.get(AiGradeSuggestion, suggestion_id)
    if row is None or row.school_id != _sid(user):
        raise NotFoundError("Suggestion not found")
    if row.status != "SUGGESTED":
        raise ConflictError("This suggestion was already decided")
    return row


@router.post("/grading/{suggestion_id}/approve")
async def approve(suggestion_id: UUID, body: ApproveBody, db: DB, user: Teacher):
    """The teacher's decision. Records the mark through the normal grading path (scope, notifications, progress)."""
    row = await _load(db, user, suggestion_id)
    marks = body.final_marks if body.final_marks is not None else row.suggested_marks
    await HomeworkService(db).grade(
        school_id=_sid(user), user_id=user.id, role=user.role.value, submission_id=row.submission_id,
        data=SubmissionGrade(marks=marks, feedback=body.feedback),
    )
    from datetime import datetime, timezone

    row.status, row.final_marks, row.decided_by, row.decided_at = "APPROVED", marks, user.id, datetime.now(timezone.utc)
    await audit(
        db, user, action="grading.approved", resource_type="ai_grade_suggestion",
        resource_id=str(row.id), message="Teacher approved a mark",
        details={"suggested": row.suggested_marks, "final": marks, "edited": marks != row.suggested_marks},
    )
    return _out(row)


@router.post("/grading/{suggestion_id}/reject")
async def reject(suggestion_id: UUID, db: DB, user: Teacher):
    row = await _load(db, user, suggestion_id)
    from datetime import datetime, timezone

    row.status, row.decided_by, row.decided_at = "REJECTED", user.id, datetime.now(timezone.utc)
    await audit(
        db, user, action="grading.rejected", resource_type="ai_grade_suggestion",
        resource_id=str(row.id), message="Teacher rejected an AI suggestion",
    )
    return _out(row)
