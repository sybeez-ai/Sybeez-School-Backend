"""Private employee documents: upload, metadata, replace, archive and authorised download."""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, NotFoundError
from src.db.models import EmployeeDocument
from src.db.models.hr import EMPLOYEE_DOCUMENT_CATEGORIES
from src.modules.documents.service import MAX_FILE_BYTES
from src.modules.employees.lookup import (
    employee_briefs,
    employee_for_user,
    get_employee_brief,
    list_employee_briefs,
    user_names,
)
from src.modules.hr_files import storage
from src.modules.hr_files.schemas import (
    ChecklistRow,
    DocumentChecklist,
    EmployeeDocumentOut,
    EmployeeDocumentUpdate,
)
from src.modules.hr_notifications.service import HR_DOCUMENT, HrNotifier
from src.modules.hr_settings.service import load_rules

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx", ".txt"}

async def read_upload(file: UploadFile) -> tuple[bytes, str, str, str | None]:
    """Validated bytes, display name, extension and MIME type (derived from the extension)."""
    name = PurePath((file.filename or "").replace("\\", "/")).name.strip()[:200]
    ext = PurePath(name).suffix.lower()
    if not name or ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in ALLOWED_EXTENSIONS))
        raise AppError(f"Unsupported file type. Allowed: {allowed}", status_code=422)
    data = await file.read(MAX_FILE_BYTES + 1)
    if not data:
        raise AppError("The file is empty", status_code=422)
    if len(data) > MAX_FILE_BYTES:
        raise AppError(
            f"File is too large (max {MAX_FILE_BYTES // 1_000_000} MB)", status_code=413
        )
    return data, name, ext, mimetypes.guess_type(name)[0] or "application/octet-stream"


class EmployeeDocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── reads ──────────────────────────────────────────────────────────────

    async def list(
        self,
        school_id: UUID,
        *,
        employee_id: UUID | None = None,
        category: str | None = None,
        status: str | None = "ACTIVE",
        q: str | None = None,
    ) -> list[EmployeeDocumentOut]:
        stmt = select(EmployeeDocument).where(EmployeeDocument.school_id == school_id)
        if employee_id:
            stmt = stmt.where(EmployeeDocument.employee_id == employee_id)
        if category:
            stmt = stmt.where(EmployeeDocument.category == category.upper())
        if status:
            stmt = stmt.where(EmployeeDocument.status == status.upper())
        rows = list(
            (
                await self.db.execute(
                    stmt.order_by(EmployeeDocument.created_at.desc()).limit(1000)
                )
            ).scalars()
        )
        out = await self._many_out(school_id, rows)
        if q:
            needle = q.strip().lower()
            out = [
                o
                for o in out
                if needle in o.title.lower()
                or needle in o.file_name.lower()
                or needle in o.employee_name.lower()
                or needle in (o.employee_code or "").lower()
            ]
        return out

    async def get(self, school_id: UUID, doc_id: UUID) -> EmployeeDocumentOut:
        return (await self._many_out(school_id, [await self._get(school_id, doc_id)]))[
            0
        ]

    async def checklist(
        self, school_id: UUID, required: tuple[str, ...] | None = None
    ) -> DocumentChecklist:
        if required is None:
            required = (await load_rules(self.db, school_id)).required_document_categories
        required = tuple(required)
        emps = await list_employee_briefs(self.db, school_id, active_only=True)
        counts = (
            await self.db.execute(
                select(
                    EmployeeDocument.employee_id,
                    EmployeeDocument.category,
                    func.count(),
                )
                .where(
                    EmployeeDocument.school_id == school_id,
                    EmployeeDocument.status == "ACTIVE",
                )
                .group_by(EmployeeDocument.employee_id, EmployeeDocument.category)
            )
        ).all()
        by_emp: dict[UUID, dict[str, int]] = {}
        for eid, cat, n in counts:
            by_emp.setdefault(eid, {})[cat] = n
        rows = []
        for e in emps:
            have = by_emp.get(e.id, {})
            rows.append(
                ChecklistRow(
                    employee_id=e.id,
                    employee_name=e.full_name,
                    employee_code=e.employee_code,
                    department_name=e.department_name,
                    staff_type=e.staff_type,
                    document_count=sum(have.values()),
                    present=[c for c in EMPLOYEE_DOCUMENT_CATEGORIES if c in have],
                    missing=[c for c in required if c not in have],
                )
            )
        rows.sort(key=lambda r: (-len(r.missing), r.employee_name.lower()))
        complete = sum(1 for r in rows if not r.missing)
        return DocumentChecklist(
            required=list(required),
            complete=complete,
            incomplete=len(rows) - complete,
            rows=rows,
        )

    async def my_documents(
        self, school_id: UUID, user_id: UUID
    ) -> list[EmployeeDocumentOut]:
        emp = await employee_for_user(self.db, school_id, user_id)
        rows = list(
            (
                await self.db.execute(
                    select(EmployeeDocument)
                    .where(
                        EmployeeDocument.school_id == school_id,
                        EmployeeDocument.employee_id == emp.id,
                        EmployeeDocument.status == "ACTIVE",
                        EmployeeDocument.shared_with_employee.is_(True),
                    )
                    .order_by(EmployeeDocument.created_at.desc())
                )
            ).scalars()
        )
        return await self._many_out(school_id, rows)

    async def download(
        self, school_id: UUID, doc_id: UUID, *, user_id: UUID, is_admin: bool
    ) -> EmployeeDocument:
        doc = await self._get(school_id, doc_id)
        if not is_admin:
            emp = await employee_for_user(self.db, school_id, user_id)
            if (
                doc.employee_id != emp.id
                or doc.status != "ACTIVE"
                or not doc.shared_with_employee
            ):
                raise NotFoundError("Document not found")
        if not storage.path_for(doc.storage_key).is_file():
            raise NotFoundError("The stored file is missing; upload it again")
        return doc

    # ── writes ─────────────────────────────────────────────────────────────

    async def upload(
        self,
        school_id: UUID,
        user_id: UUID,
        *,
        employee_id: UUID,
        category: str,
        title: str | None,
        notes: str | None,
        shared_with_employee: bool,
        file: UploadFile,
    ) -> EmployeeDocumentOut:
        emp = await get_employee_brief(self.db, school_id, employee_id)
        data, name, ext, mime = await read_upload(file)
        key = storage.save(school_id, data, ext)
        doc = EmployeeDocument(
            school_id=school_id,
            employee_id=employee_id,
            category=category,
            title=(title or "").strip() or PurePath(name).stem[:200] or name,
            notes=(notes or "").strip() or None,
            status="ACTIVE",
            shared_with_employee=shared_with_employee,
            file_name=name,
            mime_type=mime,
            file_size=len(data),
            storage_key=key,
            uploaded_by=user_id,
            updated_by=user_id,
        )
        self.db.add(doc)
        try:
            await self.db.flush()
        except Exception:
            storage.remove(key)
            raise
        if shared_with_employee:
            await self._notify_shared(doc, emp.user_id, user_id, "HR shared a document with you")
        return await self._reload_out(school_id, doc)

    async def update(
        self, school_id: UUID, user_id: UUID, doc_id: UUID, data: EmployeeDocumentUpdate
    ) -> EmployeeDocumentOut:
        doc = await self._get(school_id, doc_id)
        was_shared = doc.shared_with_employee
        payload = data.model_dump(exclude_unset=True)
        if "title" in payload and not payload["title"]:
            raise AppError("Title is required", status_code=422)
        for k, v in payload.items():
            if k == "notes":
                v = v or None
            elif v is None:
                continue
            setattr(doc, k, v)
        doc.updated_by = user_id
        await self.db.flush()
        if doc.shared_with_employee and not was_shared and doc.status == "ACTIVE":
            emp = await get_employee_brief(self.db, school_id, doc.employee_id)
            await self._notify_shared(doc, emp.user_id, user_id, "HR shared a document with you")
        return await self._reload_out(school_id, doc)

    async def replace_file(
        self, school_id: UUID, user_id: UUID, doc_id: UUID, file: UploadFile
    ) -> EmployeeDocumentOut:
        doc = await self._get(school_id, doc_id)
        if doc.status != "ACTIVE":
            raise AppError(
                "Restore the document before replacing its file", status_code=422
            )
        data, name, ext, mime = await read_upload(file)
        old_key = doc.storage_key
        doc.storage_key = storage.save(school_id, data, ext)
        doc.file_name, doc.mime_type, doc.file_size = name, mime, len(data)
        doc.updated_by = user_id
        await self.db.flush()
        storage.remove(old_key)
        if doc.shared_with_employee:
            emp = await get_employee_brief(self.db, school_id, doc.employee_id)
            await self._notify_shared(doc, emp.user_id, user_id, "HR updated one of your documents")
        return await self._reload_out(school_id, doc)

    async def set_archived(
        self, school_id: UUID, user_id: UUID, doc_id: UUID, archived: bool
    ) -> EmployeeDocumentOut:
        doc = await self._get(school_id, doc_id)
        target = "ARCHIVED" if archived else "ACTIVE"
        if doc.status == target:
            raise AppError(f"Document is already {target.lower()}", status_code=422)
        doc.status = target
        doc.archived_at = datetime.now(UTC) if archived else None
        doc.updated_by = user_id
        await self.db.flush()
        return await self._reload_out(school_id, doc)

    async def delete(self, school_id: UUID, doc_id: UUID) -> None:
        doc = await self._get(school_id, doc_id)
        if doc.status != "ARCHIVED":
            raise AppError(
                "Archive the document before deleting it permanently", status_code=422
            )
        key = doc.storage_key
        await self.db.delete(doc)
        await self.db.flush()
        storage.remove(key)

    # ── internals ──────────────────────────────────────────────────────────

    async def _notify_shared(
        self, doc: EmployeeDocument, employee_user: UUID | None, actor: UUID, title: str
    ) -> None:
        await HrNotifier(self.db).to_employee(
            doc.school_id,
            employee_user,
            kind="document",
            category=HR_DOCUMENT,
            title=title,
            body=f"{doc.title} ({doc.file_name}) is available in My HR.",
            entity_id=doc.id,
            actor=actor,
        )

    async def _get(self, school_id: UUID, doc_id: UUID) -> EmployeeDocument:
        doc = (
            await self.db.execute(
                select(EmployeeDocument).where(
                    EmployeeDocument.id == doc_id,
                    EmployeeDocument.school_id == school_id,
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            raise NotFoundError("Document not found")
        return doc

    async def _reload_out(
        self, school_id: UUID, doc: EmployeeDocument
    ) -> EmployeeDocumentOut:
        fresh = (
            await self.db.execute(
                select(EmployeeDocument)
                .where(EmployeeDocument.id == doc.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return (await self._many_out(school_id, [fresh]))[0]

    async def _many_out(
        self, school_id: UUID, rows: list[EmployeeDocument]
    ) -> list[EmployeeDocumentOut]:
        emps = await employee_briefs(self.db, school_id, {r.employee_id for r in rows})
        users = await user_names(
            self.db, {u for r in rows for u in (r.uploaded_by, r.updated_by)}
        )
        out = []
        for r in rows:
            emp = emps.get(r.employee_id)
            out.append(
                EmployeeDocumentOut(
                    id=r.id,
                    employee_id=r.employee_id,
                    employee_name=emp.full_name if emp else "—",
                    employee_code=emp.employee_code if emp else None,
                    department_name=emp.department_name if emp else None,
                    category=r.category,
                    title=r.title,
                    notes=r.notes,
                    status=r.status,
                    shared_with_employee=r.shared_with_employee,
                    file_name=r.file_name,
                    mime_type=r.mime_type,
                    file_size=r.file_size,
                    uploaded_by_name=users.get(r.uploaded_by)
                    if r.uploaded_by
                    else None,
                    updated_by_name=users.get(r.updated_by) if r.updated_by else None,
                    archived_at=r.archived_at,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
            )
        return out
