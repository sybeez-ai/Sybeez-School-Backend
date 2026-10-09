from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.intelligence.schemas import CohortIntelligenceReport, IntelligenceReport
from src.modules.intelligence.service import IntelligenceService
from src.shared.enums import UserRole

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

_PRIVILEGED = (UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.PARENT)
_COHORT = (UserRole.SCHOOL_ADMIN, UserRole.TEACHER)


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/me", response_model=IntelligenceReport)
async def my_intelligence(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    """
    Learning intelligence report for the authenticated student.

    Returns:
    - **focus_areas**: subjects with overall < 50 % with tailored practice prompts
    - **strengths**: subjects with overall ≥ 75 %
    - **practice_prompts**: one actionable suggestion per weak subject
    - **subject_breakdown**: per-subject homework_avg / exam_avg / overall
    - **recent_homework_rate**: fraction of published homework submitted
    - **recent_exam_avg**: mean % across available exam marks
    - **overall**: grand mean across all subjects
    """
    return await IntelligenceService(db).get_my_intelligence(
        school_id=_sid(user), user_id=user.id
    )


@router.get("/students/{student_id}", response_model=IntelligenceReport)
async def student_intelligence(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_PRIVILEGED))],
):
    """
    Learning intelligence report for a specific student.

    ACL:
    - **school_admin**: any student in the school
    - **teacher**: students in their assigned classes
    - **parent**: their linked children only
    """
    return await IntelligenceService(db).get_student_intelligence(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        student_id=student_id,
    )


@router.get("/cohort", response_model=CohortIntelligenceReport)
async def cohort_intelligence(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_COHORT))],
):
    """
    Cohort learning intelligence for school admins (whole school) or teachers
    (assigned classes only). Built from StudentProgress overall metrics.
    """
    return await IntelligenceService(db).get_cohort_intelligence(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
    )
