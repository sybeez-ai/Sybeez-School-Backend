from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.exams.pdf import build_report_card_pdf
from src.modules.exams.schemas import (
    AuditLogOut,
    CalculateResultsOut,
    ExamCreate,
    ExamOut,
    ExamOverviewOut,
    ExamScheduleCreate,
    ExamScheduleOut,
    ExamSubjectCreate,
    ExamSubjectOut,
    ExamSubjectUpdate,
    ExamTypeCreate,
    ExamTypeOut,
    ExamTypeUpdate,
    ExamUpdate,
    GradingScaleCreate,
    GradingScaleOut,
    GradingScaleUpdate,
    MarksCorrectionIn,
    MarksSaveIn,
    MarksSheetOut,
    MarkRowOut,
    PublishOut,
    PublishResultsIn,
    ChildBriefOut,
    StudentExamScheduleItemOut,
    TeacherExamOut,
    UnlockMarksIn,
)
from src.modules.exams.service import ExamService
from src.shared.enums import UserRole
from src.modules.access.deps import public

router = APIRouter(prefix="/exams", tags=["exams"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
@public
async def exams_health():
    return {"module": "exams", "status": "ok"}


@router.get("/me/schedule", response_model=list[StudentExamScheduleItemOut])
async def my_exam_schedule(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await ExamService(db).list_my_schedule(_sid(user), user.id)


@router.get("/parent/children", response_model=list[ChildBriefOut])
async def parent_exam_children(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await ExamService(db).list_children(_sid(user), user.id)


@router.get("/parent/schedule", response_model=list[StudentExamScheduleItemOut])
async def parent_exam_schedule(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await ExamService(db).list_child_schedule(_sid(user), user.id, student_id)


# ── exam types ───────────────────────────────────────────────


@router.get("/types", response_model=list[ExamTypeOut])
async def list_exam_types(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    active_only: bool = Query(False),
):
    return await ExamService(db).list_exam_types(_sid(user), active_only=active_only)


@router.post("/types", response_model=ExamTypeOut, status_code=201)
async def create_exam_type(
    body: ExamTypeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).create_exam_type(_sid(user), body)


@router.patch("/types/{type_id}", response_model=ExamTypeOut)
async def update_exam_type(
    type_id: UUID,
    body: ExamTypeUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).update_exam_type(_sid(user), type_id, body)


# ── grading scales ───────────────────────────────────────────


@router.get("/grading-scales", response_model=list[GradingScaleOut])
async def list_grading_scales(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).list_grading_scales(_sid(user))


@router.post("/grading-scales", response_model=GradingScaleOut, status_code=201)
async def create_grading_scale(
    body: GradingScaleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).create_grading_scale(_sid(user), body)


@router.patch("/grading-scales/{scale_id}", response_model=GradingScaleOut)
async def update_grading_scale(
    scale_id: UUID,
    body: GradingScaleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).update_grading_scale(_sid(user), scale_id, body)


# ── teacher ──────────────────────────────────────────────────


@router.get("/mine", response_model=list[TeacherExamOut])
async def my_exams(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await ExamService(db).list_teacher_exams(_sid(user), user.id)


# ── exams CRUD ───────────────────────────────────────────────


@router.get("", response_model=list[ExamOut])
async def list_exams(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    academic_year_id: UUID | None = Query(None),
    status: str | None = Query(None),
    grade_id: UUID | None = Query(None),
):
    return await ExamService(db).list_exams(
        _sid(user),
        academic_year_id=academic_year_id,
        status=status,
        grade_id=grade_id,
    )


@router.post("", response_model=ExamOut, status_code=201)
async def create_exam(
    body: ExamCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).create_exam(_sid(user), body, created_by=user.id)


@router.get("/{exam_id}", response_model=ExamOut)
async def get_exam(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
):
    return await ExamService(db).get_exam(_sid(user), exam_id)


@router.patch("/{exam_id}", response_model=ExamOut)
async def update_exam(
    exam_id: UUID,
    body: ExamUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).update_exam(_sid(user), exam_id, body)


@router.delete("/{exam_id}", status_code=204)
async def delete_exam(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await ExamService(db).delete_exam(_sid(user), exam_id)


# ── subjects ─────────────────────────────────────────────────


@router.post("/{exam_id}/subjects", response_model=ExamSubjectOut, status_code=201)
async def add_subject(
    exam_id: UUID,
    body: ExamSubjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).add_subject(_sid(user), exam_id, body)


@router.patch("/{exam_id}/subjects/{subject_id}", response_model=ExamSubjectOut)
async def update_subject(
    exam_id: UUID,
    subject_id: UUID,
    body: ExamSubjectUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).update_subject(_sid(user), exam_id, subject_id, body)


@router.delete("/{exam_id}/subjects/{subject_id}", status_code=204)
async def delete_subject(
    exam_id: UUID,
    subject_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await ExamService(db).delete_subject(_sid(user), exam_id, subject_id)


# ── schedule ─────────────────────────────────────────────────


@router.get("/{exam_id}/schedule", response_model=list[ExamScheduleOut])
async def get_schedule(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
):
    return await ExamService(db).list_schedule(_sid(user), exam_id)


@router.post("/{exam_id}/schedule", response_model=list[ExamScheduleOut])
async def set_schedule(
    exam_id: UUID,
    body: list[ExamScheduleCreate],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).upsert_schedule(_sid(user), exam_id, body)


# ── marks ────────────────────────────────────────────────────


@router.get("/{exam_id}/marks", response_model=MarksSheetOut)
async def get_marks(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    exam_subject_id: UUID = Query(...),
    class_id: UUID = Query(...),
):
    return await ExamService(db).get_marks_sheet(
        _sid(user),
        exam_id,
        exam_subject_id=exam_subject_id,
        class_id=class_id,
        user_id=user.id,
        role=user.role.value,
    )


@router.post("/{exam_id}/marks", response_model=MarksSheetOut)
async def save_marks(
    exam_id: UUID,
    body: MarksSaveIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await ExamService(db).save_marks_draft(_sid(user), exam_id, user.id, body)


@router.post("/{exam_id}/marks/submit", response_model=MarksSheetOut)
async def submit_marks(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
    exam_subject_id: UUID = Query(...),
    class_id: UUID = Query(...),
):
    return await ExamService(db).submit_marks(
        _sid(user),
        exam_id,
        user.id,
        exam_subject_id=exam_subject_id,
        class_id=class_id,
    )


@router.post("/{exam_id}/marks/unlock", response_model=MarksSheetOut)
async def unlock_marks(
    exam_id: UUID,
    body: UnlockMarksIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).unlock_marks(_sid(user), exam_id, user.id, body)


@router.post("/{exam_id}/marks/correct", response_model=MarkRowOut)
async def correct_mark(
    exam_id: UUID,
    body: MarksCorrectionIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).correct_mark(_sid(user), exam_id, user.id, body)


# ── publish / overview / audit ───────────────────────────────


@router.get("/{exam_id}/overview", response_model=ExamOverviewOut)
async def exam_overview(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).overview(_sid(user), exam_id)


@router.post("/{exam_id}/open", response_model=ExamOut)
async def open_exam_for_marks(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).open_for_marks(_sid(user), exam_id)


@router.post("/{exam_id}/calculate", response_model=CalculateResultsOut)
async def calculate_results(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    allow_incomplete: bool = Query(False),
):
    return await ExamService(db).calculate_results(
        _sid(user), exam_id, allow_incomplete=allow_incomplete
    )


@router.post("/{exam_id}/publish", response_model=PublishOut)
async def publish_exam(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    body: PublishResultsIn | None = None,
):
    payload = body or PublishResultsIn()
    return await ExamService(db).publish(
        _sid(user),
        exam_id,
        user_id=user.id,
        allow_incomplete=payload.allow_incomplete,
    )


@router.post("/{exam_id}/unlock", response_model=ExamOut)
async def unlock_exam(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    from src.shared.enums import ExamStatus

    svc = ExamService(db)
    exam = await svc._get_exam(_sid(user), exam_id)
    exam.locked = False
    if exam.status == ExamStatus.PUBLISHED.value:
        exam.status = ExamStatus.UNDER_REVIEW.value
    await db.commit()
    return await svc.get_exam(_sid(user), exam_id)


@router.get("/{exam_id}/audit", response_model=list[AuditLogOut])
async def exam_audit(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ExamService(db).audit_logs(_sid(user), exam_id)


@router.get("/{exam_id}/report-card/pdf")
async def report_card_pdf_admin(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    student_id: UUID = Query(...),
):
    payload = await ExamService(db).report_card_payload(_sid(user), exam_id, student_id)
    pdf = build_report_card_pdf(payload)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report-card-{exam_id}-{student_id}.pdf"'
        },
    )
