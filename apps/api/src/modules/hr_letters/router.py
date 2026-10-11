from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr.audit import SELF_SERVICE_ROLES
from src.modules.hr_letters.schemas import (
    LETTER_TYPE_PATTERN,
    LetterCreate,
    LetterIssue,
    LetterOut,
    LetterRevoke,
    LetterTemplateOut,
    LetterTemplateSave,
    LetterTemplateSummary,
    LetterTemplateVersionOut,
    LetterType,
    LetterUpdate,
)
from src.modules.hr_letters.service import HrLetterService
from src.shared.enums import UserRole

router = APIRouter(prefix="/hr", tags=["hr-letters"])

Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]
Staff = Annotated[
    CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, *SELF_SERVICE_ROLES))
]
Teacher = Annotated[CurrentUser, Depends(require_roles(*SELF_SERVICE_ROLES))]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/letters", response_model=list[LetterOut])
async def list_letters(
    user: Admin,
    db: Db,
    employee_id: UUID | None = None,
    letter_type: Annotated[str | None, Query(pattern=LETTER_TYPE_PATTERN)] = None,
    status: Annotated[str | None, Query(pattern="(?i)^(DRAFT|ISSUED|REVOKED)$")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return await HrLetterService(db).list(
        _sid(user), employee_id=employee_id, letter_type=letter_type, status=status, q=q
    )


@router.get("/letters/template", response_model=LetterTemplateOut)
async def letter_template(
    user: Admin, db: Db, employee_id: UUID, letter_type: LetterType
):
    return await HrLetterService(db).template(
        _sid(user), employee_id, letter_type, user.full_name
    )


@router.get("/letter-templates", response_model=list[LetterTemplateSummary])
async def list_letter_templates(user: Admin, db: Db):
    return await HrLetterService(db).list_templates(_sid(user))


@router.get("/letter-templates/{letter_type}/versions", response_model=list[LetterTemplateVersionOut])
async def letter_template_versions(letter_type: LetterType, user: Admin, db: Db):
    return await HrLetterService(db).template_versions(_sid(user), letter_type)


@router.post("/letter-templates/{letter_type}", response_model=LetterTemplateVersionOut, status_code=201)
async def save_letter_template(letter_type: LetterType, body: LetterTemplateSave, user: Admin, db: Db):
    return await HrLetterService(db).save_template(_sid(user), user.id, letter_type, body)


@router.post("/letters", response_model=LetterOut, status_code=201)
async def create_letter(body: LetterCreate, user: Admin, db: Db):
    return await HrLetterService(db).create(_sid(user), user.id, user.full_name, body)


@router.get("/letters/{letter_id}", response_model=LetterOut)
async def get_letter(letter_id: UUID, user: Admin, db: Db):
    return await HrLetterService(db).get(_sid(user), letter_id)


@router.patch("/letters/{letter_id}", response_model=LetterOut)
async def update_letter(letter_id: UUID, body: LetterUpdate, user: Admin, db: Db):
    return await HrLetterService(db).update(_sid(user), user.id, letter_id, body)


@router.post("/letters/{letter_id}/issue", response_model=LetterOut)
async def issue_letter(letter_id: UUID, body: LetterIssue, user: Admin, db: Db):
    return await HrLetterService(db).issue(_sid(user), user.id, letter_id, body)


@router.post("/letters/{letter_id}/revoke", response_model=LetterOut)
async def revoke_letter(letter_id: UUID, body: LetterRevoke, user: Admin, db: Db):
    return await HrLetterService(db).revoke(_sid(user), user.id, letter_id, body)


@router.delete("/letters/{letter_id}", status_code=204)
async def delete_letter(letter_id: UUID, user: Admin, db: Db):
    await HrLetterService(db).delete(_sid(user), user.id, letter_id)
    return Response(status_code=204)


@router.get("/letters/{letter_id}/pdf")
async def letter_pdf(letter_id: UUID, user: Staff, db: Db):
    content, filename = await HrLetterService(db).pdf(
        _sid(user),
        letter_id,
        user_id=user.id,
        is_admin=user.role == UserRole.SCHOOL_ADMIN,
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/me/letters", response_model=list[LetterOut])
async def my_letters(user: Teacher, db: Db):
    return await HrLetterService(db).my_letters(_sid(user), user.id)
