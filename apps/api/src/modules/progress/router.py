from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.progress.schemas import (
    PracticeAttemptCreate,
    PracticeAttemptOut,
    ProgressRowOut,
    ProgressSummaryOut,
    RecomputeResult,
)
from src.modules.progress.service import ProgressService
from src.shared.enums import UserRole

router = APIRouter(prefix="/progress", tags=["progress"])

_PRIVILEGED = (UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.PARENT)


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


# ── My progress (student self-service) ───────────────────────────────────────


@router.get("/me", response_model=list[ProgressRowOut])
async def my_progress(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    """Return all raw StudentProgress rows for the authenticated student."""
    return await ProgressService(db).get_my_progress(
        school_id=_sid(user), user_id=user.id
    )


@router.get("/summary/me", response_model=ProgressSummaryOut)
async def my_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    """Return a summarised per-subject progress view for the authenticated student."""
    return await ProgressService(db).get_my_summary(
        school_id=_sid(user), user_id=user.id
    )


# ── Per-student (admin / teacher / parent) ────────────────────────────────────


@router.get("/students/{student_id}", response_model=list[ProgressRowOut])
async def student_progress(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_PRIVILEGED))],
):
    """Raw progress rows for a specific student.  ACL enforced per role."""
    return await ProgressService(db).get_student_progress(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        student_id=student_id,
    )


@router.get("/summary/{student_id}", response_model=ProgressSummaryOut)
async def student_summary(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_PRIVILEGED))],
):
    """Summarised progress for a specific student.  ACL enforced per role."""
    return await ProgressService(db).get_student_summary(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        student_id=student_id,
    )


# ── Recompute (admin + teacher for own students) ──────────────────────────────


@router.post("/recompute", response_model=RecomputeResult)
async def recompute_progress(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    student_id: UUID = Query(...),
    academic_year_id: UUID | None = Query(None),
):
    """
    Recompute StudentProgress rows for a student from:
    - graded homework submissions  →  `homework_avg` per subject
    - locked / submitted exam marks →  `exam_avg` per subject
    - overall = mean of available metrics per subject

    Teachers may only recompute students in their assigned classes.
    """
    svc = ProgressService(db)
    if user.role == UserRole.TEACHER:
        await svc._assert_teacher_can_view(
            school_id=_sid(user), user_id=user.id, student_id=student_id
        )
    return await svc.recompute(
        school_id=_sid(user),
        student_id=student_id,
        academic_year_id=academic_year_id,
    )


# ── Practice attempts (student) ───────────────────────────────────────────────


@router.post("/practice-attempts", response_model=PracticeAttemptOut, status_code=201)
async def create_practice_attempt(
    body: PracticeAttemptCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    """Persist a completed AI practice session for the authenticated student."""
    return await ProgressService(db).create_practice_attempt(
        school_id=_sid(user),
        user_id=user.id,
        data=body,
    )


@router.get("/practice-attempts", response_model=list[PracticeAttemptOut])
async def list_practice_attempts(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
    limit: int = Query(10, ge=1, le=50),
):
    """List recent practice attempts for the authenticated student."""
    return await ProgressService(db).list_practice_attempts(
        school_id=_sid(user),
        user_id=user.id,
        limit=limit,
    )
