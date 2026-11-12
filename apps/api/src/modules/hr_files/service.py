"""Private employee documents: upload, metadata, verification, expiry, archive and authorised download."""

from __future__ import annotations

import mimetypes
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePath
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, NotFoundError
from src.db.models import EmployeeDocument
from src.db.models.hr import EMPLOYEE_DOCUMENT_CATEGORIES, SENSITIVE_DOCUMENT_CATEGORIES
from src.modules.documents.service import MAX_FILE_BYTES
from src.modules.employees.lookup import (
    employee_briefs,
    employee_for_user,
    get_employee_brief,
    list_employee_briefs,
    user_names,
)
from src.modules.hr.audit import hr_audit
from src.modules.hr_files import storage
from src.modules.hr_files.schemas import (
    ChecklistRow,
    DocumentChecklist,
    DocumentVerify,
    DownloadLink,
    EmployeeDocumentOut,
    EmployeeDocumentUpdate,
)
from src.modules.hr_notifications.service import HR_DOCUMENT, HrNotifier
from src.modules.hr_settings.service import load_rules

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx", ".txt"}
SIGNED_DOWNLOAD_PATH = "/api/v1/hr/files/signed/"


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


def is_sensitive(category: str) -> bool:
    return category in SENSITIVE_DOCUMENT_CATEGORIES


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
        verification: str | None = None,
        expiry: str | None = None,
        q: str | None = None,
    ) -> list[EmployeeDocumentOut]:
        stmt = select(EmployeeDocument).where(EmployeeDocument.school_id == school_id)
        if employee_id:
            stmt = stmt.where(EmployeeDocument.employee_id == employee_id)
        if category:
            stmt = stmt.where(EmployeeDocument.category == category.upper())
        if status:
            stmt = stmt.where(EmployeeDocument.status == status.upper())
        if verification:
            stmt = stmt.where(EmployeeDocument.verification_status == verification.upper())
        if expiry:
            today = date.today()
            if expiry.upper() == "EXPIRED":
                stmt = stmt.where(EmployeeDocument.expiry_date < today)
            else:
                warn = (await load_rules(self.db, school_id)).document_expiry_warning_days
                stmt = stmt.where(
                    EmployeeDocument.expiry_date >= today,
                    EmployeeDocument.expiry_date <= today + timedelta(days=warn),
                )
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
        """Required categories per active employee. Expired or rejected files do not count."""
        if required is None:
            required = (await load_rules(self.db, school_id)).required_document_categories
        required = tuple(required)
        emps = await list_employee_briefs(self.db, school_id, active_only=True)
        today = date.today()
        valid = (EmployeeDocument.verification_status != "REJECTED") & or_(
            EmployeeDocument.expiry_date.is_(None), EmployeeDocument.expiry_date >= today
        )
        counts = (
            await self.db.execute(
                select(
                    EmployeeDocument.employee_id,
                    EmployeeDocument.category,
                    func.count().filter(valid),
                    func.count(),
                )
                .where(
                    EmployeeDocument.school_id == school_id,
                    EmployeeDocument.status == "ACTIVE",
                )
                .group_by(EmployeeDocument.employee_id, EmployeeDocument.category)
            )
        ).all()
        by_emp: dict[UUID, dict[str, tuple[int, int]]] = {}
        for eid, cat, n_valid, n_all in counts:
            by_emp.setdefault(eid, {})[cat] = (n_valid, n_all)
        rows = []
        for e in emps:
            have = by_emp.get(e.id, {})
            ok = {c for c, (v, _) in have.items() if v}
            rows.append(
                ChecklistRow(
                    employee_id=e.id,
                    employee_name=e.full_name,
                    employee_code=e.employee_code,
                    department_name=e.department_name,
                    staff_type=e.staff_type,
                    document_count=sum(n for _, n in have.values()),
                    present=[c for c in EMPLOYEE_DOCUMENT_CATEGORIES if c in ok],
                    missing=[c for c in required if c not in ok],
                    expired=[c for c in EMPLOYEE_DOCUMENT_CATEGORIES if c in have and c not in ok],
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
        """The document if this user may read it now; sensitive reads are audited."""
        doc = await self._get(school_id, doc_id)
        if not is_admin:
            emp = await employee_for_user(self.db, school_id, user_id)
            if (
                doc.employee_id != emp.id
                or doc.status != "ACTIVE"
                or not doc.shared_with_employee
            ):
                raise NotFoundError("Document not found")
        if not storage.exists(doc.storage_key):
            raise NotFoundError("The stored file is missing; upload it again")
        if is_sensitive(doc.category):
            await self._audit(
                doc, user_id, "downloaded", f"Sensitive document '{doc.title}' accessed",
                {"as_admin": is_admin},
            )
        return doc

    async def download_link(
        self, school_id: UUID, doc_id: UUID, *, user_id: UUID, is_admin: bool
    ) -> DownloadLink:
        doc = await self.download(school_id, doc_id, user_id=user_id, is_admin=is_admin)
        url = storage.presigned_url(doc.storage_key, doc.file_name, doc.mime_type)
        if url:
            return DownloadLink(url=url, expires_in=storage.link_ttl(), storage="s3")
        token = storage.issue_download_token(
            doc_id=doc.id, school_id=school_id, user_id=user_id, as_admin=is_admin
        )
        return DownloadLink(url=SIGNED_DOWNLOAD_PATH + token, expires_in=storage.link_ttl(), storage="local")

    async def redeem_token(self, token: str) -> EmployeeDocument:
        """Resolve a signed download token, re-checking access in case it changed since issue."""
        try:
            claims = storage.read_download_token(token)
            doc_id, school_id, user_id = UUID(claims["doc"]), UUID(claims["school_id"]), UUID(claims["sub"])
        except (ValueError, KeyError):
            raise NotFoundError("This download link is invalid or has expired") from None
        as_admin = bool(claims.get("adm"))
        if as_admin and not await self._is_admin(school_id, user_id):
            raise NotFoundError("Document not found")
        doc = await self._get(school_id, doc_id)
        if not as_admin:
            emp = await employee_for_user(self.db, school_id, user_id)
            if doc.employee_id != emp.id or doc.status != "ACTIVE" or not doc.shared_with_employee:
                raise NotFoundError("Document not found")
        if not storage.exists(doc.storage_key):
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
        expiry_date: date | None = None,
    ) -> EmployeeDocumentOut:
        emp = await get_employee_brief(self.db, school_id, employee_id)
        data, name, ext, mime = await read_upload(file)
        key = storage.save(school_id, data, ext, mime)
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
            expiry_date=expiry_date,
            verification_status="PENDING",
            uploaded_by=user_id,
            updated_by=user_id,
        )
        self.db.add(doc)
        try:
            await self.db.flush()
        except Exception:
            storage.remove(key)
            raise
        await self._audit(
            doc, user_id, "uploaded", f"'{doc.title}' uploaded for {emp.full_name}",
            {"file_name": name, "file_size": len(data), "shared_with_employee": shared_with_employee},
        )
        if shared_with_employee:
            await self._notify_shared(doc, emp.user_id, user_id, "HR shared a document with you", "shared")
        return await self._reload_out(school_id, doc)

    async def update(
        self, school_id: UUID, user_id: UUID, doc_id: UUID, data: EmployeeDocumentUpdate
    ) -> EmployeeDocumentOut:
        doc = await self._get(school_id, doc_id)
        was_shared = doc.shared_with_employee
        payload = data.model_dump(exclude_unset=True)
        if "title" in payload and not payload["title"]:
            raise AppError("Title is required", status_code=422)
        before = {k: getattr(doc, k) for k in payload}
        for k, v in payload.items():
            if k in ("notes", "expiry_date"):
                v = v or None
            elif v is None:
                continue
            setattr(doc, k, v)
        doc.updated_by = user_id
        await self.db.flush()
        changes = {k: {"from": before[k], "to": getattr(doc, k)} for k in payload if before[k] != getattr(doc, k)}
        if changes:
            await self._audit(doc, user_id, "updated", f"'{doc.title}' details updated", {"changes": changes})
        if doc.shared_with_employee and not was_shared and doc.status == "ACTIVE":
            emp = await get_employee_brief(self.db, school_id, doc.employee_id)
            await self._notify_shared(doc, emp.user_id, user_id, "HR shared a document with you", "shared")
        return await self._reload_out(school_id, doc)

    async def verify(
        self, school_id: UUID, user_id: UUID, doc_id: UUID, data: DocumentVerify
    ) -> EmployeeDocumentOut:
        doc = await self._get(school_id, doc_id)
        if doc.status != "ACTIVE":
            raise AppError("Restore the document before verifying it", status_code=422)
        if data.status == "REJECTED" and not data.note:
            raise AppError("Add a note explaining why the document was rejected", status_code=422)
        previous = doc.verification_status
        doc.verification_status = data.status
        doc.verified_by = user_id
        doc.verified_at = datetime.now(UTC)
        doc.verification_note = data.note
        doc.updated_by = user_id
        await self.db.flush()
        await self._audit(
            doc, user_id, f"verification.{data.status.lower()}",
            f"'{doc.title}' marked {data.status.lower()}",
            {"from": previous, "to": data.status, "note": data.note},
        )
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
        old_key, old_name = doc.storage_key, doc.file_name
        doc.storage_key = storage.save(school_id, data, ext, mime)
        doc.file_name, doc.mime_type, doc.file_size = name, mime, len(data)
        # A new file has not been checked yet.
        doc.verification_status = "PENDING"
        doc.verified_by = doc.verified_at = doc.verification_note = None
        doc.updated_by = user_id
        await self.db.flush()
        storage.remove(old_key)
        await self._audit(
            doc, user_id, "replaced", f"File of '{doc.title}' replaced",
            {"from": old_name, "to": name, "file_size": len(data)},
        )
        if doc.shared_with_employee:
            emp = await get_employee_brief(self.db, school_id, doc.employee_id)
            await self._notify_shared(
                doc, emp.user_id, user_id, "HR updated one of your documents", f"replaced:{doc.storage_key}"
            )
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
        await self._audit(doc, user_id, "archived" if archived else "restored", f"'{doc.title}' {target.lower()}")
        return await self._reload_out(school_id, doc)

    async def delete(self, school_id: UUID, user_id: UUID, doc_id: UUID) -> None:
        doc = await self._get(school_id, doc_id)
        if doc.status != "ARCHIVED":
            raise AppError(
                "Archive the document before deleting it permanently", status_code=422
            )
        key = doc.storage_key
        await self._audit(
            doc, user_id, "deleted", f"'{doc.title}' permanently deleted",
            {"file_name": doc.file_name, "employee_id": doc.employee_id},
        )
        await self.db.delete(doc)
        await self.db.flush()
        storage.remove(key)

    # ── internals ──────────────────────────────────────────────────────────

    async def _is_admin(self, school_id: UUID, user_id: UUID) -> bool:
        """Re-check a signed download against the same HR role and grant rules as the API."""
        from src.api.dependencies import CurrentUser
        from src.db.models import SchoolMembership, User
        from src.modules.hr.access import user_is_hr_admin
        from src.shared.enums import MembershipStatus, UserRole

        user = await self.db.get(User, user_id)
        if user is None:
            return False
        membership = (
            await self.db.execute(
                select(SchoolMembership).where(
                    SchoolMembership.school_id == school_id,
                    SchoolMembership.user_id == user_id,
                    SchoolMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).scalars().first()
        try:
            role = UserRole(membership.role) if membership is not None else UserRole.STAFF
        except ValueError:
            role = UserRole.STAFF
        current = CurrentUser(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=role,
            school_id=school_id,
            membership_id=membership.id if membership is not None else None,
            is_platform_admin=bool(user.is_platform_admin),
        )
        return await user_is_hr_admin(self.db, current)

    async def _audit(
        self, doc: EmployeeDocument, user_id: UUID, verb: str, message: str, details: dict | None = None
    ) -> None:
        await hr_audit(
            self.db,
            school_id=doc.school_id,
            actor_id=user_id,
            action=f"hr.document.{verb}",
            resource_type="employee_document",
            resource_id=doc.id,
            message=message,
            details={
                "employee_id": doc.employee_id,
                "category": doc.category,
                "sensitive": is_sensitive(doc.category),
                **(details or {}),
            },
        )

    async def _notify_shared(
        self, doc: EmployeeDocument, employee_user: UUID | None, actor: UUID, title: str, event: str
    ) -> None:
        await HrNotifier(self.db).to_employee(
            doc.school_id,
            employee_user,
            kind="document",
            category=HR_DOCUMENT,
            title=title,
            body=f"{doc.title} ({doc.file_name}) is available in My HR.",
            entity_id=doc.id,
            event=event,
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
            self.db, {u for r in rows for u in (r.uploaded_by, r.updated_by, r.verified_by)}
        )
        warn = (await load_rules(self.db, school_id)).document_expiry_warning_days if rows else 0
        today = date.today()
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
                    is_sensitive=is_sensitive(r.category),
                    title=r.title,
                    notes=r.notes,
                    status=r.status,
                    shared_with_employee=r.shared_with_employee,
                    file_name=r.file_name,
                    mime_type=r.mime_type,
                    file_size=r.file_size,
                    verification_status=r.verification_status,
                    verified_by_name=users.get(r.verified_by) if r.verified_by else None,
                    verified_at=r.verified_at,
                    verification_note=r.verification_note,
                    expiry_date=r.expiry_date,
                    is_expired=bool(r.expiry_date and r.expiry_date < today),
                    expires_soon=bool(
                        r.expiry_date and today <= r.expiry_date <= today + timedelta(days=warn)
                    ),
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
