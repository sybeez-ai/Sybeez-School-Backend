from datetime import date
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr.audit import SELF_SERVICE_ROLES
from src.modules.hr_files import storage
from src.modules.hr_files.schemas import (
    CATEGORY_PATTERN,
    DocumentChecklist,
    DocumentVerify,
    DownloadLink,
    EmployeeDocumentOut,
    EmployeeDocumentUpdate,
)
from src.modules.hr_files.service import EmployeeDocumentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/hr", tags=["hr-files"])

Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]
Staff = Annotated[
    CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, *SELF_SERVICE_ROLES))
]
Teacher = Annotated[CurrentUser, Depends(require_roles(*SELF_SERVICE_ROLES))]
Db = Annotated[AsyncSession, Depends(get_db)]
_PRIVATE_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


def _file_response(doc) -> Response:
    fallback = doc.file_name.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")
    return Response(
        content=storage.read(doc.storage_key),
        media_type=doc.mime_type or "application/octet-stream",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(doc.file_name)}",
        },
    )


@router.get("/files", response_model=list[EmployeeDocumentOut])
async def list_files(
    user: Admin,
    db: Db,
    employee_id: UUID | None = None,
    category: Annotated[str | None, Query(pattern=CATEGORY_PATTERN)] = None,
    status: Annotated[str, Query(pattern="^(ACTIVE|ARCHIVED|ALL)$")] = "ACTIVE",
    verification: Annotated[str | None, Query(pattern="^(PENDING|VERIFIED|REJECTED)$")] = None,
    expiry: Annotated[str | None, Query(pattern="^(EXPIRED|EXPIRING)$")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return await EmployeeDocumentService(db).list(
        _sid(user),
        employee_id=employee_id,
        category=category,
        status=None if status == "ALL" else status,
        verification=verification,
        expiry=expiry,
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
    expiry_date: Annotated[date | None, Form()] = None,
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
        expiry_date=expiry_date,
    )


@router.get("/files/signed/{token}")
async def signed_download(token: str, db: Db):
    """Exchange a short-lived signed link for the file (no session needed; the token is the grant)."""
    doc = await EmployeeDocumentService(db).redeem_token(token)
    return _file_response(doc)


@router.get("/files/{doc_id}", response_model=EmployeeDocumentOut)
async def get_file(doc_id: UUID, user: Admin, db: Db):
    return await EmployeeDocumentService(db).get(_sid(user), doc_id)


@router.patch("/files/{doc_id}", response_model=EmployeeDocumentOut)
async def update_file(doc_id: UUID, body: EmployeeDocumentUpdate, user: Admin, db: Db):
    return await EmployeeDocumentService(db).update(_sid(user), user.id, doc_id, body)


@router.post("/files/{doc_id}/verify", response_model=EmployeeDocumentOut)
async def verify_file(doc_id: UUID, body: DocumentVerify, user: Admin, db: Db):
    return await EmployeeDocumentService(db).verify(_sid(user), user.id, doc_id, body)


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
    await EmployeeDocumentService(db).delete(_sid(user), user.id, doc_id)
    return Response(status_code=204)


@router.post("/files/{doc_id}/link", response_model=DownloadLink)
async def file_link(doc_id: UUID, user: Staff, db: Db):
    """A download URL valid for a short time (S3 presigned, or a signed one-file token)."""
    return await EmployeeDocumentService(db).download_link(
        _sid(user), doc_id, user_id=user.id, is_admin=user.role == UserRole.SCHOOL_ADMIN
    )


@router.get("/files/{doc_id}/download")
async def download_file(doc_id: UUID, user: Staff, db: Db):
    doc = await EmployeeDocumentService(db).download(
        _sid(user), doc_id, user_id=user.id, is_admin=user.role == UserRole.SCHOOL_ADMIN
    )
    return _file_response(doc)


@router.get("/me/files", response_model=list[EmployeeDocumentOut])
async def my_files(user: Teacher, db: Db):
    return await EmployeeDocumentService(db).my_documents(_sid(user), user.id)
