"""Business logic for documents / study materials."""

from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    ClassRoom,
    Document,
    Enrollment,
    Parent,
    Student,
    StudentParent,
    Subject,
    Teacher,
    TeacherAssignment,
)
from src.modules.documents.repository import DocumentRepository
from src.modules.documents.schemas import DocumentCreate, DocumentOut, DocumentUpdate
from src.shared.enums import EnrollmentStatus, UserRole

MAX_FILE_BYTES = 20_000_000  # 20 MB


def _file_url(file_key: str | None) -> str | None:
    from src.core.storage import public_url

    return public_url(file_key)


class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = DocumentRepository(db)

    # ── identity helpers ──────────────────────────────────────────────────────

    async def _teacher_for_user(self, school_id: UUID, user_id: UUID) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise ForbiddenError("Teacher profile required")
        return teacher

    async def _student_for_user(self, school_id: UUID, user_id: UUID) -> Student:
        student = (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not student:
            raise ForbiddenError("Student profile required")
        return student

    async def _parent_for_user(self, school_id: UUID, user_id: UUID) -> Parent:
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        return parent

    async def _active_enrollment(
        self, school_id: UUID, student_id: UUID
    ) -> Enrollment | None:
        return (
            await self.db.execute(
                select(Enrollment)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id == student_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .order_by(Enrollment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _teacher_class_ids(self, school_id: UUID, teacher_id: UUID) -> set[UUID]:
        assignments = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher_id,
                )
            )
        ).scalars().all()
        # Also check class_teacher_id
        class_teacher_rows = (
            await self.db.execute(
                select(ClassRoom).where(
                    ClassRoom.school_id == school_id,
                    ClassRoom.class_teacher_id == teacher_id,
                )
            )
        ).scalars().all()
        ids: set[UUID] = {a.class_id for a in assignments}
        ids.update(c.id for c in class_teacher_rows)
        return ids

    # ── serializer ───────────────────────────────────────────────────────────

    def _to_out(self, doc: Document) -> DocumentOut:
        # Prefer file_key-derived URL, fall back to stored file_url
        url = _file_url(doc.file_key) or (doc.file_url if doc.file_url else None)
        return DocumentOut(
            id=doc.id,
            school_id=doc.school_id,
            uploaded_by=doc.uploaded_by,
            title=doc.title,
            category=doc.category,
            description=doc.description,
            visibility=doc.visibility,
            is_active=doc.is_active,
            file_url=doc.file_url or None,
            file_key=doc.file_key,
            file_name=doc.file_name,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            class_id=doc.class_id,
            subject_id=doc.subject_id,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            url=url,
        )

    # ── LIST ─────────────────────────────────────────────────────────────────

    async def list_documents(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        category: str | None = None,
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
        q: str | None = None,
    ) -> list[DocumentOut]:
        # ── school admin: full view ──────────────────────────────────────────
        if role == UserRole.SCHOOL_ADMIN.value:
            docs = await self.repo.list_any_visibility(
                school_id=school_id, category=category,
                class_id=class_id, subject_id=subject_id, q=q,
            )
            return [self._to_out(d) for d in docs]

        # ── teacher: their class docs + TEACHERS/ALL/SCHOOL visibility ───────
        if role == UserRole.TEACHER.value:
            teacher = await self._teacher_for_user(school_id, user_id)
            teacher_class_ids = await self._teacher_class_ids(school_id, teacher.id)

            if class_id is not None and class_id not in teacher_class_ids:
                raise ForbiddenError("You are not assigned to this class")

            # Global visibility docs (TEACHERS / ALL / SCHOOL)
            global_docs = await self.repo.list(
                school_id=school_id, category=category,
                class_id=None, subject_id=subject_id, q=q,
                active_only=True,
                visibility_values=["TEACHERS", "ALL", "SCHOOL"],
            )

            # Class-specific docs (any visibility) for their classes
            class_docs: list[Document] = []
            target_classes = [class_id] if class_id else list(teacher_class_ids)
            for cid in target_classes:
                class_docs.extend(
                    await self.repo.list(
                        school_id=school_id, category=category,
                        class_id=cid, subject_id=subject_id, q=q,
                        active_only=True, visibility_values=None,
                    )
                )

            # Merge, deduplicate
            seen: set[UUID] = {d.id for d in global_docs}
            merged = list(global_docs)
            for d in class_docs:
                if d.id not in seen:
                    merged.append(d)
                    seen.add(d.id)
            merged.sort(key=lambda d: d.created_at, reverse=True)
            return [self._to_out(d) for d in merged]

        # ── student: class docs visible to STUDENTS / ALL ────────────────────
        if role == UserRole.STUDENT.value:
            student = await self._student_for_user(school_id, user_id)
            enr = await self._active_enrollment(school_id, student.id)
            student_class_id = enr.class_id if enr else None

            # If caller provided class_id, only allow their own class
            if class_id is not None and class_id != student_class_id:
                raise ForbiddenError("Document is not for your class")

            eff_class_id = class_id or student_class_id
            docs = await self.repo.list(
                school_id=school_id, category=category,
                class_id=eff_class_id, subject_id=subject_id, q=q,
                active_only=True, visibility_values=["STUDENTS", "ALL"],
            )
            return [self._to_out(d) for d in docs]

        # ── parent: children's class docs (STUDENTS / PARENTS / ALL) ─────────
        if role == UserRole.PARENT.value:
            parent = await self._parent_for_user(school_id, user_id)
            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == school_id,
                        StudentParent.parent_id == parent.id,
                    )
                )
            ).scalars().all()
            child_class_ids: set[UUID] = set()
            for link in links:
                enr = await self._active_enrollment(school_id, link.student_id)
                if enr:
                    child_class_ids.add(enr.class_id)

            if class_id is not None and class_id not in child_class_ids:
                raise ForbiddenError("Not linked to this class")

            target_classes = [class_id] if class_id else list(child_class_ids)
            docs_out: list[Document] = []
            seen2: set[UUID] = set()
            for cid in target_classes:
                for d in await self.repo.list(
                    school_id=school_id, category=category,
                    class_id=cid, subject_id=subject_id, q=q,
                    active_only=True,
                    visibility_values=["STUDENTS", "PARENTS", "ALL"],
                ):
                    if d.id not in seen2:
                        docs_out.append(d)
                        seen2.add(d.id)
            docs_out.sort(key=lambda d: d.created_at, reverse=True)
            return [self._to_out(d) for d in docs_out]

        raise ForbiddenError("Not allowed")

    # ── CREATE ────────────────────────────────────────────────────────────────

    async def create_document(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        data: DocumentCreate,
    ) -> DocumentOut:
        if role == UserRole.TEACHER.value:
            teacher = await self._teacher_for_user(school_id, user_id)
            if data.class_id is not None:
                class_ids = await self._teacher_class_ids(school_id, teacher.id)
                if data.class_id not in class_ids:
                    raise ForbiddenError(
                        "You are not assigned to this class. "
                        "Ask an admin to add a teaching assignment."
                    )
        elif role != UserRole.SCHOOL_ADMIN.value:
            raise ForbiddenError("Only teachers or school admins can create documents")

        if data.class_id is not None:
            classroom = await self.db.get(ClassRoom, data.class_id)
            if not classroom or classroom.school_id != school_id:
                raise NotFoundError("Class not found")
        if data.subject_id is not None:
            subject = await self.db.get(Subject, data.subject_id)
            if not subject or subject.school_id != school_id:
                raise NotFoundError("Subject not found")

        doc = Document(
            school_id=school_id,
            uploaded_by=user_id,
            title=data.title.strip(),
            category=data.category,
            description=data.description,
            visibility=data.visibility,
            is_active=True,
            file_url=data.file_url or "",
            class_id=data.class_id,
            subject_id=data.subject_id,
        )
        await self.repo.create(doc)
        return self._to_out(doc)

    # ── UPLOAD ────────────────────────────────────────────────────────────────

    async def upload_document(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        file: UploadFile,
        title: str,
        category: str = "STUDY_MATERIAL",
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
        visibility: str = "STUDENTS",
    ) -> DocumentOut:
        if role not in (UserRole.SCHOOL_ADMIN.value, UserRole.TEACHER.value):
            raise ForbiddenError("Only teachers or school admins can upload documents")

        if role == UserRole.TEACHER.value:
            teacher = await self._teacher_for_user(school_id, user_id)
            if class_id is not None:
                class_ids = await self._teacher_class_ids(school_id, teacher.id)
                if class_id not in class_ids:
                    raise ForbiddenError("You are not assigned to this class")

        from src.core.storage import put_object

        raw, name, mime, size = await self._read_upload(file)
        key_name = f"{uuid4().hex[:10]}_{name}"
        file_key = put_object(
            key=f"documents/{school_id}/{key_name}",
            data=raw,
            content_type=mime,
        )

        if class_id is not None:
            classroom = await self.db.get(ClassRoom, class_id)
            if not classroom or classroom.school_id != school_id:
                raise NotFoundError("Class not found")
        if subject_id is not None:
            subject = await self.db.get(Subject, subject_id)
            if not subject or subject.school_id != school_id:
                raise NotFoundError("Subject not found")

        doc = Document(
            school_id=school_id,
            uploaded_by=user_id,
            title=title.strip(),
            category=category,
            visibility=visibility,
            is_active=True,
            file_url=_file_url(file_key) or "",
            file_key=file_key,
            file_name=name,
            file_size=size,
            mime_type=mime,
            class_id=class_id,
            subject_id=subject_id,
        )
        await self.repo.create(doc)
        return self._to_out(doc)

    # ── GET ───────────────────────────────────────────────────────────────────

    async def get_document(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        doc_id: UUID,
    ) -> DocumentOut:
        doc = await self.repo.get(school_id, doc_id)
        if not doc or not doc.is_active:
            raise NotFoundError("Document not found")
        await self._assert_can_read(school_id=school_id, user_id=user_id, role=role, doc=doc)
        return self._to_out(doc)

    # ── UPDATE ────────────────────────────────────────────────────────────────

    async def update_document(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        doc_id: UUID,
        data: DocumentUpdate,
    ) -> DocumentOut:
        doc = await self.repo.get(school_id, doc_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, doc=doc)

        payload = data.model_dump(exclude_unset=True)
        if "class_id" in payload and payload["class_id"] is not None:
            classroom = await self.db.get(ClassRoom, payload["class_id"])
            if not classroom or classroom.school_id != school_id:
                raise NotFoundError("Class not found")
        if "subject_id" in payload and payload["subject_id"] is not None:
            subject = await self.db.get(Subject, payload["subject_id"])
            if not subject or subject.school_id != school_id:
                raise NotFoundError("Subject not found")

        for k, v in payload.items():
            setattr(doc, k, v)
        return self._to_out(doc)

    # ── DELETE (soft) ─────────────────────────────────────────────────────────

    async def delete_document(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        doc_id: UUID,
    ) -> None:
        doc = await self.repo.get(school_id, doc_id)
        if not doc:
            raise NotFoundError("Document not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, doc=doc)
        await self.repo.soft_delete(doc)

    # ── ACL helpers ───────────────────────────────────────────────────────────

    async def _assert_can_read(
        self, *, school_id: UUID, user_id: UUID, role: str, doc: Document
    ) -> None:
        if role == UserRole.SCHOOL_ADMIN.value:
            return

        if role == UserRole.TEACHER.value:
            if doc.visibility in ("TEACHERS", "ALL", "SCHOOL"):
                return
            teacher = await self._teacher_for_user(school_id, user_id)
            if doc.class_id is not None:
                class_ids = await self._teacher_class_ids(school_id, teacher.id)
                if doc.class_id in class_ids:
                    return
            raise ForbiddenError("Access denied")

        if role == UserRole.STUDENT.value:
            if doc.visibility not in ("STUDENTS", "ALL"):
                raise ForbiddenError("Access denied")
            if doc.class_id is not None:
                student = await self._student_for_user(school_id, user_id)
                enr = await self._active_enrollment(school_id, student.id)
                if not enr or enr.class_id != doc.class_id:
                    raise ForbiddenError("Document is not for your class")
            return

        if role == UserRole.PARENT.value:
            if doc.visibility not in ("PARENTS", "STUDENTS", "ALL"):
                raise ForbiddenError("Access denied")
            return

        raise ForbiddenError("Not allowed")

    async def _assert_can_manage(
        self, *, school_id: UUID, user_id: UUID, role: str, doc: Document
    ) -> None:
        if role == UserRole.SCHOOL_ADMIN.value:
            return
        if role == UserRole.TEACHER.value:
            # Owner may always manage their upload
            if doc.uploaded_by == user_id:
                return
            # Class teacher of that class may manage
            teacher = await self._teacher_for_user(school_id, user_id)
            if doc.class_id is not None:
                class_ids = await self._teacher_class_ids(school_id, teacher.id)
                if doc.class_id in class_ids:
                    return
            raise ForbiddenError("You can only manage your own documents")
        raise ForbiddenError("Not allowed")

    # ── upload util ───────────────────────────────────────────────────────────

    async def _read_upload(
        self, file: UploadFile
    ) -> tuple[bytes, str, str | None, int]:
        data = await file.read()
        if not data:
            raise ConflictError("Empty upload")
        if len(data) > MAX_FILE_BYTES:
            raise ConflictError("File too large (max 20 MB)")
        name = (file.filename or "file").replace("/", "_")[:200]
        return data, name, file.content_type, len(data)
