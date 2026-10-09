from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.documents.schemas import DocumentCreate, DocumentOut, DocumentUpdate
from src.modules.documents.service import DocumentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/documents", tags=["documents"])

_ALL_SCHOOL_ROLES = (
    UserRole.SCHOOL_ADMIN,
    UserRole.TEACHER,
    UserRole.STUDENT,
    UserRole.PARENT,
)
_WRITE_ROLES = (UserRole.SCHOOL_ADMIN, UserRole.TEACHER)


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ALL_SCHOOL_ROLES))],
    category: str | None = Query(None, description="Filter by category"),
    class_id: UUID | None = Query(None),
    subject_id: UUID | None = Query(None),
    q: str | None = Query(None, description="Title search"),
):
    """
    List documents visible to the current user.

    - **admin**: all active documents for the school.
    - **teacher**: documents for their assigned classes + TEACHERS/ALL visibility.
    - **student**: STUDENTS/ALL documents for their enrolled class.
    - **parent**: STUDENTS/PARENTS/ALL documents for their children's classes.
    """
    return await DocumentService(db).list_documents(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        category=category,
        class_id=class_id,
        subject_id=subject_id,
        q=q,
    )


# ── Create metadata ───────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=DocumentOut)
async def create_document(
    body: DocumentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_WRITE_ROLES))],
):
    """Create a document metadata record (no file upload).  Use `/upload` for file bytes."""
    return await DocumentService(db).create_document(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        data=body,
    )


# ── Upload ────────────────────────────────────────────────────────────────────


@router.post("/upload", status_code=201, response_model=DocumentOut)
async def upload_document(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_WRITE_ROLES))],
    file: UploadFile,
    title: Annotated[str, Form(min_length=1, max_length=255)],
    category: Annotated[str, Form()] = "STUDY_MATERIAL",
    visibility: Annotated[str, Form()] = "STUDENTS",
    class_id: Annotated[UUID | None, Form()] = None,
    subject_id: Annotated[UUID | None, Form()] = None,
):
    """
    Upload a file and create the associated document record in one step.

    Accepts multipart/form-data with fields: `file`, `title`, `category`,
    `class_id`, `subject_id`, `visibility`.
    """
    return await DocumentService(db).upload_document(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        file=file,
        title=title,
        category=category,
        class_id=class_id,
        subject_id=subject_id,
        visibility=visibility,
    )


# ── Get by ID ─────────────────────────────────────────────────────────────────


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ALL_SCHOOL_ROLES))],
):
    return await DocumentService(db).get_document(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        doc_id=doc_id,
    )


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{doc_id}", response_model=DocumentOut)
async def update_document(
    doc_id: UUID,
    body: DocumentUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_WRITE_ROLES))],
):
    return await DocumentService(db).update_document(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        doc_id=doc_id,
        data=body,
    )


# ── Delete (soft) ─────────────────────────────────────────────────────────────


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_WRITE_ROLES))],
):
    """Soft-delete: sets is_active=False.  Document is no longer visible to users."""
    await DocumentService(db).delete_document(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        doc_id=doc_id,
    )
    return None
