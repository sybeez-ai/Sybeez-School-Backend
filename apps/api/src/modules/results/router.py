from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.exams.pdf import build_report_card_pdf
from src.modules.exams.schemas import ChildBriefOut, StudentResultOut
from src.modules.exams.service import ExamService
from src.shared.enums import UserRole

router = APIRouter(prefix="/results", tags=["results"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
async def results_health():
    return {"module": "results", "status": "ok"}


@router.get("/me", response_model=list[StudentResultOut])
async def my_results(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await ExamService(db).my_results(_sid(user), user.id)


@router.get("/children", response_model=list[ChildBriefOut])
async def children(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await ExamService(db).list_children(_sid(user), user.id)


@router.get("/children/{student_id}", response_model=list[StudentResultOut])
async def child_results(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await ExamService(db).child_results(_sid(user), user.id, student_id)


@router.get("/student/{student_id}", response_model=list[StudentResultOut])
async def student_results_admin(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
):
    # Admin/teacher: list published results for a student in school
    svc = ExamService(db)
    from sqlalchemy import select

    from src.db.models import Exam, Result
    from src.shared.enums import ExamStatus

    results = (
        await db.execute(
            select(Result).where(
                Result.school_id == _sid(user),
                Result.student_id == student_id,
                Result.is_published.is_(True),
            )
        )
    ).scalars().all()
    out: list[StudentResultOut] = []
    for r in results:
        exam = await db.get(Exam, r.exam_id)
        if exam and exam.status == ExamStatus.PUBLISHED.value:
            out.append(await svc.result_for_student(_sid(user), r.exam_id, student_id))
    return out


@router.get("/exams/{exam_id}", response_model=list[StudentResultOut])
async def exam_results(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).exam_results_admin(_sid(user), exam_id)


@router.get("/{exam_id}/report-card")
async def report_card_json(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.STUDENT, UserRole.PARENT, UserRole.SCHOOL_ADMIN)),
    ],
    student_id: UUID | None = Query(None),
):
    svc = ExamService(db)
    sid = _sid(user)
    if user.role == UserRole.STUDENT:
        student = await svc._student_for_user(sid, user.id)
        target = student.id
    elif user.role == UserRole.PARENT:
        if not student_id:
            raise ForbiddenError("student_id required")
        parent = await svc._parent_for_user(sid, user.id)
        await svc._assert_parent_child(sid, parent.id, student_id)
        target = student_id
    else:
        if not student_id:
            raise ForbiddenError("student_id required")
        target = student_id
    return await svc.report_card_payload(sid, exam_id, target)


@router.get("/{exam_id}/report-card/pdf")
async def report_card_pdf(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.STUDENT, UserRole.PARENT, UserRole.SCHOOL_ADMIN)),
    ],
    student_id: UUID | None = Query(None),
):
    svc = ExamService(db)
    sid = _sid(user)
    if user.role == UserRole.STUDENT:
        student = await svc._student_for_user(sid, user.id)
        target = student.id
    elif user.role == UserRole.PARENT:
        if not student_id:
            raise ForbiddenError("student_id required")
        parent = await svc._parent_for_user(sid, user.id)
        await svc._assert_parent_child(sid, parent.id, student_id)
        target = student_id
    else:
        if not student_id:
            raise ForbiddenError("student_id required")
        target = student_id
    payload = await svc.report_card_payload(sid, exam_id, target)
    pdf = build_report_card_pdf(payload)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report-card-{exam_id}.pdf"'
        },
    )
