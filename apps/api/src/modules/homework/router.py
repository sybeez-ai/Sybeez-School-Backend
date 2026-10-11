from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.homework.schemas import (
    AttachmentOut,
    ChildBriefOut,
    HomeworkCreate,
    HomeworkOut,
    HomeworkReportOut,
    HomeworkUpdate,
    SubmissionCreate,
    SubmissionGrade,
    SubmissionOut,
    SubmissionStatsOut,
)
from src.modules.access.deps import require_permission
from src.modules.homework.service import HomeworkService
from src.shared.enums import UserRole
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/homework", tags=["homework"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
async def homework_health():
    return {"module": "homework", "status": "ok"}


@router.get("/mine", response_model=list[HomeworkOut])
async def my_homework(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
    subject_id: UUID | None = Query(None),
    status: str | None = Query(None, description="PENDING|SUBMITTED|OVERDUE|GRADED"),
    homework_type: str | None = Query(None),
    homework_types: str | None = Query(
        None, description="Comma-separated types e.g. ASSIGNMENT,PROJECT"
    ),
):
    types = [t.strip().upper() for t in (homework_types or "").split(",") if t.strip()] or None
    return await HomeworkService(db).list_for_actor(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        subject_id=subject_id,
        status=status,
        homework_type=homework_type.upper() if homework_type else None,
        homework_types=types,
    )


@router.get("/children", response_model=list[ChildBriefOut])
async def parent_children(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await HomeworkService(db).list_children(school_id=_sid(user), user_id=user.id)


@router.get("/children/{student_id}", response_model=list[HomeworkOut])
async def parent_child_homework(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
    homework_type: str | None = Query(None),
    homework_types: str | None = Query(None),
):
    types = [t.strip().upper() for t in (homework_types or "").split(",") if t.strip()] or None
    return await HomeworkService(db).list_for_child(
        school_id=_sid(user),
        user_id=user.id,
        student_id=student_id,
        homework_type=homework_type.upper() if homework_type else None,
        homework_types=types,
    )


@router.get("/reports", response_model=list[HomeworkReportOut])
async def homework_reports(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    class_id: UUID | None = Query(None),
    subject_id: UUID | None = Query(None),
    teacher_id: UUID | None = Query(None),
    status: str | None = Query(None),
    academic_year_id: UUID | None = Query(None),
):
    return await HomeworkService(db).reports(
        school_id=_sid(user),
        class_id=class_id,
        subject_id=subject_id,
        teacher_id=teacher_id,
        status=status,
        academic_year_id=academic_year_id,
    )


@router.get("", response_model=list[HomeworkOut])
async def list_homework(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT)),
    ],
    class_id: UUID | None = Query(None),
    subject_id: UUID | None = Query(None),
    status: str | None = Query(None),
    academic_year_id: UUID | None = Query(None),
    teacher_id: UUID | None = Query(None),
    homework_type: str | None = Query(None),
    homework_types: str | None = Query(
        None, description="Comma-separated types e.g. ASSIGNMENT,PROJECT"
    ),
):
    types = [t.strip().upper() for t in (homework_types or "").split(",") if t.strip()] or None
    return await HomeworkService(db).list_for_actor(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        class_id=class_id,
        subject_id=subject_id,
        status=status,
        academic_year_id=academic_year_id,
        teacher_id=teacher_id,
        homework_type=homework_type.upper() if homework_type else None,
        homework_types=types,
    )


@router.post("", response_model=HomeworkOut, status_code=201)
async def create_homework(
    body: HomeworkCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await HomeworkService(db).create(
        school_id=_sid(user), user_id=user.id, role=user.role.value, data=body
    )


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
async def get_submission(
    submission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = HomeworkService(db)
    sub = await svc.repo.get_submission_by_id(school_id=_sid(user), submission_id=submission_id)
    if not sub:
        from src.core.exceptions import NotFoundError

        raise NotFoundError("Submission not found")
    hw = await svc.repo.get(_sid(user), sub.homework_id)
    await svc._assert_can_manage(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework=hw
    )
    return await svc._to_submission_out(sub, hw)


@router.patch("/submissions/{submission_id}", response_model=SubmissionOut)
async def grade_submission(
    submission_id: UUID,
    body: SubmissionGrade,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await HomeworkService(db).grade(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        submission_id=submission_id,
        data=body,
    )


@router.get("/{homework_id}", response_model=HomeworkOut)
async def get_homework(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(
                UserRole.SCHOOL_ADMIN,
                UserRole.TEACHER,
                UserRole.STUDENT,
            )
        ),
    ],
):
    return await HomeworkService(db).get(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id
    )


@router.patch("/{homework_id}", response_model=HomeworkOut)
async def update_homework(
    homework_id: UUID,
    body: HomeworkUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await HomeworkService(db).update(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        homework_id=homework_id,
        data=body,
    )


@router.delete("/{homework_id}", status_code=204)
async def delete_homework(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    await HomeworkService(db).delete(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id
    )


@router.post("/{homework_id}/publish", response_model=HomeworkOut)
async def publish_homework(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await HomeworkService(db).publish(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id
    )


@router.post("/{homework_id}/close", response_model=HomeworkOut)
async def close_homework(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await HomeworkService(db).close(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id
    )


@router.get("/{homework_id}/submissions", response_model=list[SubmissionOut])
async def list_submissions(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await HomeworkService(db).list_submissions(
        school_id=_sid(user), user_id=user.id, role=user.role.value, homework_id=homework_id
    )


@router.get("/{homework_id}/stats", response_model=SubmissionStatsOut)
async def homework_stats(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await HomeworkService(db).submission_stats(school_id=_sid(user), homework_id=homework_id)


@router.get("/{homework_id}/submission", response_model=SubmissionOut | None)
async def my_submission(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await HomeworkService(db).get_my_submission(
        school_id=_sid(user), user_id=user.id, homework_id=homework_id
    )


@router.post("/{homework_id}/submission", response_model=SubmissionOut)
async def submit_homework(
    homework_id: UUID,
    body: SubmissionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await HomeworkService(db).submit(
        school_id=_sid(user), user_id=user.id, homework_id=homework_id, data=body
    )


@router.post("/{homework_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_homework_attachment(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    file: UploadFile = File(...),
):
    return await HomeworkService(db).upload_homework_file(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        homework_id=homework_id,
        file=file,
    )


@router.post("/{homework_id}/submission/attachments", response_model=AttachmentOut, status_code=201)
async def upload_submission_attachment(
    homework_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
    file: UploadFile = File(...),
):
    return await HomeworkService(db).upload_submission_file(
        school_id=_sid(user), user_id=user.id, homework_id=homework_id, file=file
    )
