from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr_files import storage
from src.modules.hr_files.schemas import (
    DocumentChecklist,
    EmployeeDocumentOut,
    EmployeeDocumentUpdate,
)
from src.modules.hr_files.service import EmployeeDocumentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/hr", tags=["hr-files"])

Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Staff = Annotated[
    CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))
]
Teacher = Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))]
Db = Annotated[AsyncSession, Depends(get_db)]
CATEGORY_PATTERN = "^(ID_PROOF|CONTRACT|QUALIFICATION|MEDICAL|BANK|OTHER)$"


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/files", response_model=list[EmployeeDocumentOut])
async def list_files(
    user: Admin,
    db: Db,
    employee_id: UUID | None = None,
    category: Annotated[str | None, Query(pattern=CATEGORY_PATTERN)] = None,
    status: Annotated[str, Query(pattern="^(ACTIVE|ARCHIVED|ALL)$")] = "ACTIVE",
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return await EmployeeDocumentService(db).list(
        _sid(user),
        employee_id=employee_id,
        category=category,
        status=None if status == "ALL" else status,
        q=q,
    )


@router.get("/files/checklist", response_model=DocumentChecklist)
async def files_checklist(user: Admin, db: Db):
    return await EmployeeDocumentService(db).checklist(_sid(user))


@router.post("/files", response_model=EmployeeDocumentOut, status_code=201)
async def upload_file(
    user: Admin,
    db: Db,
    file: UploadFile,
    employee_id: Annotated[UUID, Form()],
    category: Annotated[str, Form(pattern=CATEGORY_PATTERN)],
    title: Annotated[str | None, Form(max_length=200)] = None,
    notes: Annotated[str | None, Form(max_length=2000)] = None,
    shared_with_employee: Annotated[bool, Form()] = False,
):
    return await EmployeeDocumentService(db).upload(
        _sid(user),
        user.id,
        employee_id=employee_id,
        category=category,
        title=title,
        notes=notes,
        shared_with_employee=shared_with_employee,
        file=file,
    )


@router.get("/files/{doc_id}", response_model=EmployeeDocumentOut)
async def get_file(doc_id: UUID, user: Admin, db: Db):
    return await EmployeeDocumentService(db).get(_sid(user), doc_id)


@router.patch("/files/{doc_id}", response_model=EmployeeDocumentOut)
async def update_file(doc_id: UUID, body: EmployeeDocumentUpdate, user: Admin, db: Db):
    return await EmployeeDocumentService(db).update(_sid(user), user.id, doc_id, body)


@router.put("/files/{doc_id}/file", response_model=EmployeeDocumentOut)
async def replace_file(doc_id: UUID, user: Admin, db: Db, file: UploadFile):
    return await EmployeeDocumentService(db).replace_file(
        _sid(user), user.id, doc_id, file
    )


@router.post("/files/{doc_id}/archive", response_model=EmployeeDocumentOut)
async def archive_file(doc_id: UUID, user: Admin, db: Db):
    return await EmployeeDocumentService(db).set_archived(
        _sid(user), user.id, doc_id, True
    )


@router.post("/files/{doc_id}/restore", response_model=EmployeeDocumentOut)
async def restore_file(doc_id: UUID, user: Admin, db: Db):
    return await EmployeeDocumentService(db).set_archived(
        _sid(user), user.id, doc_id, False
    )


@router.delete("/files/{doc_id}", status_code=204)
async def delete_file(doc_id: UUID, user: Admin, db: Db):
    await EmployeeDocumentService(db).delete(_sid(user), doc_id)
    return Response(status_code=204)


@router.get("/files/{doc_id}/download")
async def download_file(doc_id: UUID, user: Staff, db: Db):
    doc = await EmployeeDocumentService(db).download(
        _sid(user), doc_id, user_id=user.id, is_admin=user.role == UserRole.SCHOOL_ADMIN
    )
    return FileResponse(
        storage.path_for(doc.storage_key),
        media_type=doc.mime_type or "application/octet-stream",
        filename=doc.file_name,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/me/files", response_model=list[EmployeeDocumentOut])
async def my_files(user: Teacher, db: Db):
    return await EmployeeDocumentService(db).my_documents(_sid(user), user.id)
