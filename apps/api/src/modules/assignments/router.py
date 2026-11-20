"""Assignments are homework rows of type ASSIGNMENT. The homework API stays for other types."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import public, require_permission
from src.modules.homework.schemas import (
    ChildBriefOut,
    HomeworkCreate,
    HomeworkOut,
    HomeworkUpdate,
    SubmissionCreate,
    SubmissionGrade,
    SubmissionOut,
    SubmissionStatsOut,
)
from src.modules.homework.service import HomeworkService
from src.shared.enums import HomeworkType, UserRole

router = APIRouter(prefix="/assignments", tags=["assignments"])
_KIND = HomeworkType.ASSIGNMENT.value


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("/health")
@public
async def assignments_health():
    return {"module": "assignments", "status": "ok"}


@router.get("/mine", response_model=list[HomeworkOut])
async def my_assignments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.read"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
    subject_id: UUID | None = Query(None),
    status: str | None = Query(None),
):
    return await HomeworkService(db).list_for_actor(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        subject_id=subject_id,
        status=status,
        homework_type=_KIND,
    )


@router.get("/children", response_model=list[ChildBriefOut])
async def assignment_children(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.read"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await HomeworkService(db).list_children(school_id=_sid(user), user_id=user.id)


@router.get("/children/{student_id}", response_model=list[HomeworkOut])
async def child_assignments(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.read"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await HomeworkService(db).list_for_child(
        school_id=_sid(user),
        user_id=user.id,
        student_id=student_id,
        homework_type=_KIND,
    )


@router.get("", response_model=list[HomeworkOut])
async def list_assignments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.read"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT))],
    class_id: UUID | None = Query(None),
    subject_id: UUID | None = Query(None),
    status: str | None = Query(None),
    academic_year_id: UUID | None = Query(None),
    teacher_id: UUID | None = Query(None),
):
    return await HomeworkService(db).list_for_actor(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        class_id=class_id,
        subject_id=subject_id,
        status=status,
        academic_year_id=academic_year_id,
        teacher_id=teacher_id,
        homework_type=_KIND,
    )


@router.post("", response_model=HomeworkOut, status_code=201)
async def create_assignment(
    body: HomeworkCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.write"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    data = body.model_copy(update={"homework_type": HomeworkType.ASSIGNMENT})
    return await HomeworkService(db).create(
        school_id=_sid(user), user_id=user.id, role=user.role.value, data=data
    )


@router.patch("/submissions/{submission_id}", response_model=SubmissionOut)
async def grade_assignment(
    submission_id: UUID,
    body: SubmissionGrade,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.grade"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = HomeworkService(db)
    sub = await svc.repo.get_submission_by_id(school_id=_sid(user), submission_id=submission_id)
    if not sub:
        from src.core.exceptions import NotFoundError

        raise NotFoundError("Submission not found")
    await svc.assert_kind(school_id=_sid(user), homework_id=sub.homework_id, kind=_KIND)
    return await svc.grade(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        submission_id=submission_id,
        data=body,
    )


@router.get("/{homework_id}", response_model=HomeworkOut)
async def get_assignment(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.read"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    return await svc.get(school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id)


@router.patch("/{homework_id}", response_model=HomeworkOut)
async def update_assignment(
    homework_id: UUID,
    body: HomeworkUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.write"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    if body.homework_type is not None and body.homework_type != HomeworkType.ASSIGNMENT:
        raise ForbiddenError("Assignments stay assignments")
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    data = body.model_copy(update={"homework_type": HomeworkType.ASSIGNMENT})
    return await svc.update(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id, data=data
    )


@router.delete("/{homework_id}", status_code=204)
async def delete_assignment(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.write"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    await svc.delete(school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id)


@router.post("/{homework_id}/publish", response_model=HomeworkOut)
async def publish_assignment(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.write"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    return await svc.publish(school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id)


@router.post("/{homework_id}/close", response_model=HomeworkOut)
async def close_assignment(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.write"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    return await svc.close(school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id)


@router.get("/{homework_id}/submissions", response_model=list[SubmissionOut])
async def list_assignment_submissions(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.grade"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    return await svc.list_submissions(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id
    )


@router.get("/{homework_id}/stats", response_model=SubmissionStatsOut)
async def assignment_stats(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.read"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    await svc.get(school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id)
    return await svc.submission_stats(school_id=_sid(user), homework_id=homework_id)


@router.get("/{homework_id}/submission", response_model=SubmissionOut | None)
async def my_assignment_submission(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.read"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    return await svc.get_my_submission(school_id=_sid(user), user_id=user.id, homework_id=homework_id)


@router.post("/{homework_id}/submission", response_model=SubmissionOut, status_code=201)
async def submit_assignment(
    homework_id: UUID,
    body: SubmissionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("assignments.submit"))],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    svc = HomeworkService(db)
    await svc.assert_kind(school_id=_sid(user), homework_id=homework_id, kind=_KIND)
    return await svc.submit(school_id=_sid(user), user_id=user.id, homework_id=homework_id, data=body)
