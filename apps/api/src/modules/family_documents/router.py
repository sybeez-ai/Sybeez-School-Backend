"""Family documents: progress cards, mark sheets, certificates, transfer certificates.

Only school admins upload. Only the student and their linked parents (and admins) can read. Teachers get 403 here
and see no rows in the database either. Files are never exposed by URL: they stream through this API after the
access check, and every upload, download and delete is audited.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core import storage
from src.core.database import get_db
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.db.models import FamilyDocument, Parent, Student, StudentParent
from src.modules.access.audit import write_audit, write_scoped_audit
from src.shared.enums import UserRole

router = APIRouter(prefix="/family-documents", tags=["family-documents"])
DB = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]
Family = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT, UserRole.STUDENT))]

TYPES = ("PROGRESS_CARD", "MARK_SHEET", "CERTIFICATE", "TC", "OTHER")
MAX_BYTES = 10_000_000
_SIGS = ((b"%PDF-", "application/pdf"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


class DocOut(BaseModel):
    id: UUID
    student_id: UUID
    doc_type: str
    title: str
    academic_year: str | None
    file_name: str
    mime_type: str
    file_size: int


def _out(d: FamilyDocument) -> DocOut:
    return DocOut(id=d.id, student_id=d.student_id, doc_type=d.doc_type, title=d.title, academic_year=d.academic_year,
                  file_name=d.file_name, mime_type=d.mime_type, file_size=d.file_size)


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


async def _audit(db, user: CurrentUser, **kw) -> None:
    try:
        async with db.begin_nested():
            await write_scoped_audit(db, **kw)
    except Exception:  # no session context (admin/service path)
        await write_audit(db, school_id=_sid(user), actor_id=user.id, **kw)


def _family_filter(user: CurrentUser):
    """Belt and braces next to RLS: parents and students are limited to their own family's student ids."""
    if user.role == UserRole.SCHOOL_ADMIN:
        return None
    if user.role == UserRole.STUDENT:
        return select(Student.id).where(Student.user_id == user.id)
    return (select(StudentParent.student_id).join(Parent, Parent.id == StudentParent.parent_id)
            .where(Parent.user_id == user.id))


def _sniff(data: bytes) -> str | None:
    for sig, mime in _SIGS:
        if data.startswith(sig):
            return mime
    return None


@router.get("", response_model=list[DocOut])
async def list_documents(db: DB, user: Family, student_id: UUID | None = None):
    q = select(FamilyDocument).where(FamilyDocument.school_id == _sid(user))
    if student_id:
        q = q.where(FamilyDocument.student_id == student_id)
    fam = _family_filter(user)
    if fam is not None:
        q = q.where(FamilyDocument.student_id.in_(fam))
    rows = (await db.execute(q.order_by(FamilyDocument.created_at.desc()).limit(200))).scalars().all()
    return [_out(r) for r in rows]  # RLS limits parents and students to their own family's rows


@router.post("", status_code=201, response_model=DocOut)
async def upload(
    db: DB, user: Admin, file: UploadFile,
    student_id: Annotated[UUID, Form()],
    doc_type: Annotated[str, Form()],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    academic_year: Annotated[str | None, Form(max_length=20)] = None,
):
    school_id = _sid(user)
    if doc_type not in TYPES:
        raise ValidationError("Unknown document type")
    student = (await db.execute(select(Student).where(Student.id == student_id, Student.school_id == school_id))).scalar_one_or_none()
    if student is None:
        raise NotFoundError("Student not found")
    data = await file.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise ValidationError("File must be between 1 byte and 10 MB")
    mime = _sniff(data)
    if mime is None:
        raise ValidationError("Only PDF, PNG or JPEG files are accepted")
    doc_id = uuid4()
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[mime]
    key = storage.put_object(key=f"schools/{school_id}/family-docs/{student_id}/{doc_id}.{ext}", data=data, content_type=mime)
    safe_name = (file.filename or f"{doc_type.lower()}.{ext}").replace("/", "_").replace("\\", "_")[:255]
    row = FamilyDocument(id=doc_id, school_id=school_id, student_id=student_id, doc_type=doc_type, title=title.strip(),
                         academic_year=academic_year, file_key=key, file_name=safe_name, mime_type=mime,
                         file_size=len(data), uploaded_by=user.id)
    db.add(row)
    await db.flush()
    await _audit(db, user, action="family_document.uploaded", resource_type="family_document", resource_id=str(doc_id),
                 message=f"{doc_type} uploaded", details={"student_id": str(student_id), "type": doc_type})
    return _out(row)


async def _get(db, user: CurrentUser, doc_id: UUID) -> FamilyDocument:
    q = select(FamilyDocument).where(FamilyDocument.id == doc_id, FamilyDocument.school_id == _sid(user))
    fam = _family_filter(user)
    if fam is not None:
        q = q.where(FamilyDocument.student_id.in_(fam))
    row = (await db.execute(q)).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Document not found")
    return row


@router.get("/{doc_id}/download")
async def download(doc_id: UUID, db: DB, user: Family):
    row = await _get(db, user, doc_id)
    try:
        data = storage.read_object(row.file_key)
    except FileNotFoundError as exc:
        raise NotFoundError("File is missing") from exc
    await _audit(db, user, action="family_document.viewed", resource_type="family_document", resource_id=str(row.id),
                 message="Document opened", details={"student_id": str(row.student_id)})
    return Response(content=data, media_type=row.mime_type, headers={
        "Content-Disposition": f'attachment; filename="{row.file_name}"', "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff"})


@router.delete("/{doc_id}", status_code=204)
async def delete(doc_id: UUID, db: DB, user: Admin):
    row = await _get(db, user, doc_id)
    file_key = row.file_key
    await db.delete(row)
    await _audit(db, user, action="family_document.deleted", resource_type="family_document", resource_id=str(doc_id),
                 message="Document deleted", details={"student_id": str(row.student_id)})
    storage.delete_object(file_key)  # the file goes with the row
    return Response(status_code=204)
