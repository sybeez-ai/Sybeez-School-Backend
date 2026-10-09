from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.students.schemas import (
    BulkImportConfirmOut,
    BulkImportConfirmRequest,
    BulkImportValidateOut,
    BulkImportValidateRequest,
    StudentCreate,
    StudentOut,
    StudentPage,
    StudentStatusUpdate,
    StudentUpdate,
)
from src.modules.students.service import StudentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/students", tags=["students"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


class EnrollBody(BaseModel):
    class_id: UUID
    academic_year_id: UUID | None = None
    roll_number: str | None = Field(default=None, max_length=32)


class TransferBody(BaseModel):
    class_id: UUID
    roll_number: str | None = Field(default=None, max_length=32)


class WithdrawBody(BaseModel):
    notes: str | None = None


@router.get("", response_model=StudentPage)
async def list_students(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    q: str | None = Query(None),
    academic_year_id: UUID | None = Query(None),
    grade_id: UUID | None = Query(None),
    class_id: UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("name"),
):
    return await StudentService(db).list(
        _sid(user),
        q=q,
        academic_year_id=academic_year_id,
        grade_id=grade_id,
        class_id=class_id,
        status=status,
        page=page,
        page_size=page_size,
        sort=sort,
        actor_user_id=user.id,
        actor_role=user.role,
    )


@router.post("", response_model=StudentOut, status_code=201)
async def create_student(
    body: StudentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).create(_sid(user), body)


@router.post("/import/validate", response_model=BulkImportValidateOut)
async def validate_student_import(
    body: BulkImportValidateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).validate_import(_sid(user), body.rows)


@router.post("/import/confirm", response_model=BulkImportConfirmOut)
async def confirm_student_import(
    body: BulkImportConfirmRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).confirm_import(_sid(user), body.rows)


@router.get("/{student_id}", response_model=StudentOut)
async def get_student(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = StudentService(db)
    school_id = _sid(user)
    if user.role == UserRole.TEACHER:
        await svc.assert_teacher_can_view(school_id, student_id, user.id)
    return await svc.get(school_id, student_id)


@router.post("/{student_id}/photo", response_model=StudentOut)
async def upload_student_photo(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    file: UploadFile = File(...),
):
    return await StudentService(db).upload_photo(_sid(user), student_id, file)


@router.patch("/{student_id}", response_model=StudentOut)
async def update_student(
    student_id: UUID,
    body: StudentUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).update(_sid(user), student_id, body)


@router.patch("/{student_id}/status", response_model=StudentOut)
async def update_student_status(
    student_id: UUID,
    body: StudentStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).set_status(
        _sid(user), student_id, status=body.status, is_active=body.is_active
    )


@router.post("/{student_id}/enroll", response_model=StudentOut)
async def enroll_student(
    student_id: UUID,
    body: EnrollBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).enroll(
        _sid(user),
        student_id,
        class_id=body.class_id,
        academic_year_id=body.academic_year_id,
        roll_number=body.roll_number,
    )


@router.post("/{student_id}/transfer", response_model=StudentOut)
async def transfer_student(
    student_id: UUID,
    body: TransferBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).transfer(
        _sid(user), student_id, class_id=body.class_id, roll_number=body.roll_number
    )


@router.post("/{student_id}/withdraw", response_model=StudentOut)
async def withdraw_student(
    student_id: UUID,
    body: WithdrawBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).withdraw(_sid(user), student_id, notes=body.notes)
