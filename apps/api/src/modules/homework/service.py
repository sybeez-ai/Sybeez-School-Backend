from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Homework,
    HomeworkAttachment,
    HomeworkSubmission,
    Parent,
    Student,
    StudentParent,
    Subject,
    SubmissionAttachment,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.homework.repository import HomeworkRepository
from src.modules.homework.schemas import (
    AttachmentOut,
    ChildBriefOut,
    HomeworkCreate,
    HomeworkOut,
    HomeworkReportOut,
    HomeworkUpdate,
    SubmissionCreate,
    SubmissionGrade,
    SubmissionOut,
    SubmissionStatsOut,
)
from src.modules.notifications.service import create_in_app_notification
from src.shared.enums import (
    EnrollmentStatus,
    HomeworkStatus,
    HomeworkSubmissionStatus,
    UserRole,
)

MEDIA_ROOT = Path(__file__).resolve().parents[3] / "media"
HOMEWORK_DIR = MEDIA_ROOT / "homework"
SUBMISSION_DIR = MEDIA_ROOT / "homework-submissions"
MAX_FILE_BYTES = 10_000_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _file_url(file_key: str) -> str:
    if file_key.startswith("/"):
        return file_key
    return f"/media/{file_key}"


class HomeworkService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = HomeworkRepository(db)

    # ── identity helpers ─────────────────────────────────────

    async def _user_name(self, user_id: UUID | None) -> str | None:
        if not user_id:
            return None
        user = await self.db.get(User, user_id)
        return user.full_name if user else None

    async def _teacher_name(self, teacher_id: UUID | None) -> str | None:
        if not teacher_id:
            return None
        teacher = await self.db.get(Teacher, teacher_id)
        return await self._user_name(teacher.user_id) if teacher else None

    async def _student_name(self, student_id: UUID) -> str | None:
        student = await self.db.get(Student, student_id)
        return await self._user_name(student.user_id) if student else None

    async def _class_name(self, class_id: UUID | None) -> str | None:
        if not class_id:
            return None
        classroom = await self.db.get(ClassRoom, class_id)
        return classroom.name if classroom else None

    async def _subject_name(self, subject_id: UUID | None) -> str | None:
        if not subject_id:
            return None
        subject = await self.db.get(Subject, subject_id)
        return subject.name if subject else None

    async def teacher_for_user(self, *, school_id: UUID, user_id: UUID) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise ForbiddenError("Teacher profile required")
        return teacher

    async def student_for_user(self, *, school_id: UUID, user_id: UUID) -> Student:
        student = (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not student:
            raise ForbiddenError("Student profile required")
        return student

    async def parent_for_user(self, *, school_id: UUID, user_id: UUID) -> Parent:
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        return parent

    async def assert_parent_of(
        self, *, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> None:
        parent = await self.parent_for_user(school_id=school_id, user_id=user_id)
        link = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent.id,
                    StudentParent.student_id == student_id,
                )
            )
        ).scalar_one_or_none()
        if not link:
            raise ForbiddenError("Not linked to this student")

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

    async def _assert_teacher_can_assign(
        self, *, school_id: UUID, teacher: Teacher, class_id: UUID, subject_id: UUID
    ) -> ClassRoom:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Section not found")
        subject = await self.db.get(Subject, subject_id)
        if not subject or subject.school_id != school_id:
            raise NotFoundError("Subject not found")

        assigned = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == class_id,
                    TeacherAssignment.subject_id == subject_id,
                )
            )
        ).scalar_one_or_none()
        if assigned:
            return classroom
        if classroom.class_teacher_id == teacher.id:
            return classroom
        # Also allow any assignment to this class (subject flexibility for MVP)
        any_class = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == class_id,
                )
            )
        ).scalar_one_or_none()
        if any_class:
            return classroom
        raise ForbiddenError(
            "You are not assigned to teach this subject/section. Ask admin to add a teaching assignment."
        )

    # ── serializers ──────────────────────────────────────────

    def _att_out(self, row: HomeworkAttachment | SubmissionAttachment) -> AttachmentOut:
        return AttachmentOut(
            id=row.id,
            file_name=row.file_name,
            file_key=row.file_key,
            mime_type=row.mime_type,
            file_size=row.file_size,
            url=_file_url(row.file_key),
        )

    async def _to_homework_out(
        self,
        row: Homework,
        *,
        with_stats: bool = False,
        my_submission: HomeworkSubmission | None = None,
    ) -> HomeworkOut:
        out = HomeworkOut(
            id=row.id,
            school_id=row.school_id,
            academic_year_id=row.academic_year_id,
            class_id=row.class_id,
            class_name=await self._class_name(row.class_id),
            subject_id=row.subject_id,
            subject_name=await self._subject_name(row.subject_id),
            teacher_id=row.teacher_id,
            teacher_name=await self._teacher_name(row.teacher_id),
            title=row.title,
            description=row.description,
            homework_type=row.homework_type,
            assigned_at=row.assigned_at,
            due_at=row.due_at,
            max_marks=row.max_marks,
            allow_resubmission=row.allow_resubmission,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
            attachments=[self._att_out(a) for a in (row.attachments or [])],
        )
        if with_stats:
            stats = await self.submission_stats(school_id=row.school_id, homework_id=row.id)
            out.submitted_count = stats.submitted + stats.late + stats.graded + stats.returned
            out.pending_count = stats.pending
            out.late_count = stats.late
            out.graded_count = stats.graded
            out.total_students = stats.total
        if my_submission:
            out.my_submission_status = my_submission.status
            out.my_marks = my_submission.marks
        elif with_stats is False and my_submission is None:
            pass
        return out

    async def _to_submission_out(
        self, row: HomeworkSubmission, homework: Homework | None = None
    ) -> SubmissionOut:
        hw = homework or await self.db.get(Homework, row.homework_id)
        return SubmissionOut(
            id=row.id,
            homework_id=row.homework_id,
            student_id=row.student_id,
            student_name=await self._student_name(row.student_id),
            school_id=row.school_id,
            content=row.content,
            submitted_at=row.submitted_at,
            status=row.status,
            marks=row.marks,
            feedback=row.feedback,
            reviewed_by=row.reviewed_by,
            reviewed_by_name=await self._user_name(row.reviewed_by),
            reviewed_at=row.reviewed_at,
            created_at=row.created_at,
            attachments=[self._att_out(a) for a in (row.attachments or [])],
            homework_title=hw.title if hw else None,
            max_marks=hw.max_marks if hw else None,
        )

    # ── CRUD ─────────────────────────────────────────────────

    async def create(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        data: HomeworkCreate,
    ) -> HomeworkOut:
        if role == UserRole.TEACHER.value:
            teacher = await self.teacher_for_user(school_id=school_id, user_id=user_id)
            classroom = await self._assert_teacher_can_assign(
                school_id=school_id,
                teacher=teacher,
                class_id=data.class_id,
                subject_id=data.subject_id,
            )
        elif role == UserRole.SCHOOL_ADMIN.value:
            classroom = await self.db.get(ClassRoom, data.class_id)
            if not classroom or classroom.school_id != school_id:
                raise NotFoundError("Section not found")
            subject = await self.db.get(Subject, data.subject_id)
            if not subject or subject.school_id != school_id:
                raise NotFoundError("Subject not found")
            teacher = None
            if data.teacher_id:
                teacher = await self.db.get(Teacher, data.teacher_id)
                if not teacher or teacher.school_id != school_id:
                    raise NotFoundError("Teacher not found")
            elif classroom.class_teacher_id:
                teacher = await self.db.get(Teacher, classroom.class_teacher_id)
            else:
                asg = (
                    await self.db.execute(
                        select(TeacherAssignment)
                        .where(
                            TeacherAssignment.school_id == school_id,
                            TeacherAssignment.class_id == data.class_id,
                            TeacherAssignment.subject_id == data.subject_id,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                teacher = await self.db.get(Teacher, asg.teacher_id) if asg else None
            if not teacher:
                raise AppError(
                    "No teacher available for this section/subject. Assign a teacher first.",
                    code="no_teacher",
                    status_code=400,
                )
        else:
            raise ForbiddenError("Only teachers or school admins can create homework")

        year_id = data.academic_year_id or classroom.academic_year_id
        if year_id:
            year = await self.db.get(AcademicYear, year_id)
            if not year or year.school_id != school_id:
                raise NotFoundError("Academic year not found")

        now = _utcnow()
        row = Homework(
            school_id=school_id,
            academic_year_id=year_id,
            class_id=data.class_id,
            subject_id=data.subject_id,
            teacher_id=teacher.id,
            syllabus_topic_id=data.syllabus_topic_id,
            title=data.title.strip(),
            description=data.description.strip() if data.description else None,
            homework_type=data.homework_type.value,
            assigned_at=data.assigned_at or (now if data.status == HomeworkStatus.PUBLISHED else None),
            due_at=data.due_at,
            max_marks=data.max_marks,
            allow_resubmission=data.allow_resubmission,
            status=data.status.value,
        )
        await self.repo.create(row)
        if row.status == HomeworkStatus.PUBLISHED.value:
            await self._notify_published(row)
        return await self._to_homework_out(row, with_stats=True)

    async def update(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        homework_id: UUID,
        data: HomeworkUpdate,
    ) -> HomeworkOut:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, homework=row)

        payload = data.model_dump(exclude_unset=True)
        if "homework_type" in payload and payload["homework_type"] is not None:
            payload["homework_type"] = (
                payload["homework_type"].value
                if hasattr(payload["homework_type"], "value")
                else payload["homework_type"]
            )
        if "title" in payload and payload["title"]:
            payload["title"] = payload["title"].strip()
        if "description" in payload and payload["description"]:
            payload["description"] = payload["description"].strip()

        new_class = payload.get("class_id", row.class_id)
        new_subject = payload.get("subject_id", row.subject_id)
        if role == UserRole.TEACHER.value and ("class_id" in payload or "subject_id" in payload):
            teacher = await self.teacher_for_user(school_id=school_id, user_id=user_id)
            await self._assert_teacher_can_assign(
                school_id=school_id, teacher=teacher, class_id=new_class, subject_id=new_subject
            )

        for k, v in payload.items():
            setattr(row, k, v)
        return await self._to_homework_out(row, with_stats=True)

    async def publish(
        self, *, school_id: UUID, user_id: UUID, role: str, homework_id: UUID
    ) -> HomeworkOut:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, homework=row)
        if not row.title.strip():
            raise AppError("Title is required", code="validation_error", status_code=400)
        if row.status == HomeworkStatus.PUBLISHED.value:
            return await self._to_homework_out(row, with_stats=True)
        row.status = HomeworkStatus.PUBLISHED.value
        if not row.assigned_at:
            row.assigned_at = _utcnow()
        await self._notify_published(row)
        return await self._to_homework_out(row, with_stats=True)

    async def close(
        self, *, school_id: UUID, user_id: UUID, role: str, homework_id: UUID
    ) -> HomeworkOut:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, homework=row)
        row.status = HomeworkStatus.CLOSED.value
        return await self._to_homework_out(row, with_stats=True)

    async def delete(
        self, *, school_id: UUID, user_id: UUID, role: str, homework_id: UUID
    ) -> None:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, homework=row)
        await self.repo.delete(row)

    async def _assert_can_manage(
        self, *, school_id: UUID, user_id: UUID, role: str, homework: Homework
    ) -> None:
        if role == UserRole.SCHOOL_ADMIN.value:
            return
        if role == UserRole.TEACHER.value:
            teacher = await self.teacher_for_user(school_id=school_id, user_id=user_id)
            if homework.teacher_id != teacher.id:
                raise ForbiddenError("You can only manage your own homework")
            return
        raise ForbiddenError("Not allowed")

    async def get(
        self, *, school_id: UUID, user_id: UUID, role: str, homework_id: UUID
    ) -> HomeworkOut:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        my_sub = None
        if role == UserRole.STUDENT.value:
            student = await self.student_for_user(school_id=school_id, user_id=user_id)
            enr = await self._active_enrollment(school_id, student.id)
            if not enr or enr.class_id != row.class_id:
                raise ForbiddenError("Homework is not for your section")
            if row.status == HomeworkStatus.DRAFT.value:
                raise NotFoundError("Homework not found")
            my_sub = await self.repo.get_submission(
                school_id=school_id, homework_id=row.id, student_id=student.id
            )
        elif role == UserRole.PARENT.value:
            raise ForbiddenError("Use parent child endpoints")
        elif role == UserRole.TEACHER.value:
            teacher = await self.teacher_for_user(school_id=school_id, user_id=user_id)
            if row.teacher_id != teacher.id:
                asg = (
                    await self.db.execute(
                        select(TeacherAssignment)
                        .where(
                            TeacherAssignment.school_id == school_id,
                            TeacherAssignment.teacher_id == teacher.id,
                            TeacherAssignment.class_id == row.class_id,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                classroom = await self.db.get(ClassRoom, row.class_id)
                is_ct = bool(classroom and classroom.class_teacher_id == teacher.id)
                if not asg and not is_ct:
                    raise ForbiddenError("Not your homework")
        return await self._to_homework_out(row, with_stats=True, my_submission=my_sub)

    async def list_for_actor(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
        status: str | None = None,
        academic_year_id: UUID | None = None,
        teacher_id: UUID | None = None,
        homework_type: str | None = None,
        homework_types: list[str] | None = None,
    ) -> list[HomeworkOut]:
        if role == UserRole.TEACHER.value:
            teacher = await self.teacher_for_user(school_id=school_id, user_id=user_id)
            rows = await self.repo.list(
                school_id=school_id,
                class_id=class_id,
                subject_id=subject_id,
                teacher_id=teacher.id,
                status=status,
                academic_year_id=academic_year_id,
                homework_type=homework_type,
                homework_types=homework_types,
            )
            return [await self._to_homework_out(r, with_stats=True) for r in rows]

        if role == UserRole.SCHOOL_ADMIN.value:
            rows = await self.repo.list(
                school_id=school_id,
                class_id=class_id,
                subject_id=subject_id,
                teacher_id=teacher_id,
                status=status,
                academic_year_id=academic_year_id,
                homework_type=homework_type,
                homework_types=homework_types,
            )
            return [await self._to_homework_out(r, with_stats=True) for r in rows]

        if role == UserRole.STUDENT.value:
            student = await self.student_for_user(school_id=school_id, user_id=user_id)
            enr = await self._active_enrollment(school_id, student.id)
            if not enr:
                return []
            rows = await self.repo.list_for_section(
                school_id=school_id,
                class_id=enr.class_id,
                homework_type=homework_type,
                homework_types=homework_types,
            )
            out: list[HomeworkOut] = []
            now = _utcnow()
            for r in rows:
                if subject_id and r.subject_id != subject_id:
                    continue
                sub = await self.repo.get_submission(
                    school_id=school_id, homework_id=r.id, student_id=student.id
                )
                item = await self._to_homework_out(r, my_submission=sub)
                if not sub:
                    item.my_submission_status = HomeworkSubmissionStatus.PENDING.value
                eff = item.my_submission_status or HomeworkSubmissionStatus.PENDING.value
                if status == "PENDING" and eff not in (
                    HomeworkSubmissionStatus.PENDING.value,
                    HomeworkSubmissionStatus.RETURNED.value,
                ):
                    continue
                if status == "SUBMITTED" and eff not in (
                    HomeworkSubmissionStatus.SUBMITTED.value,
                    HomeworkSubmissionStatus.LATE.value,
                ):
                    continue
                if status == "GRADED" and eff != HomeworkSubmissionStatus.GRADED.value:
                    continue
                if status == "OVERDUE":
                    if eff in (
                        HomeworkSubmissionStatus.SUBMITTED.value,
                        HomeworkSubmissionStatus.LATE.value,
                        HomeworkSubmissionStatus.GRADED.value,
                    ):
                        continue
                    if not r.due_at:
                        continue
                    due = r.due_at if r.due_at.tzinfo else r.due_at.replace(tzinfo=timezone.utc)
                    if due >= now:
                        continue
                out.append(item)
            return out

        raise ForbiddenError("Not allowed")

    # ── submissions ──────────────────────────────────────────

    async def submission_stats(
        self, *, school_id: UUID, homework_id: UUID
    ) -> SubmissionStatsOut:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        total = await self.repo.count_enrolled(school_id, row.class_id)
        subs = await self.repo.list_submissions(school_id=school_id, homework_id=homework_id)
        submitted = late = graded = returned = 0
        student_ids = set()
        for s in subs:
            student_ids.add(s.student_id)
            if s.status == HomeworkSubmissionStatus.LATE.value:
                late += 1
                submitted += 1
            elif s.status == HomeworkSubmissionStatus.SUBMITTED.value:
                submitted += 1
            elif s.status == HomeworkSubmissionStatus.GRADED.value:
                graded += 1
                submitted += 1
            elif s.status == HomeworkSubmissionStatus.RETURNED.value:
                returned += 1
                submitted += 1
        pending = max(0, total - len(student_ids))
        # Students with PENDING record
        pending += sum(1 for s in subs if s.status == HomeworkSubmissionStatus.PENDING.value)
        # Adjust: pending should be those without real submission
        with_work = {
            s.student_id
            for s in subs
            if s.status != HomeworkSubmissionStatus.PENDING.value
        }
        pending = max(0, total - len(with_work))
        return SubmissionStatsOut(
            total=total,
            submitted=len(with_work),
            pending=pending,
            late=late,
            graded=graded,
            returned=returned,
        )

    async def list_submissions(
        self, *, school_id: UUID, user_id: UUID, role: str, homework_id: UUID
    ) -> list[SubmissionOut]:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, homework=row)

        # Include enrolled students without submission as synthetic PENDING
        enrollments = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.school_id == school_id,
                    Enrollment.class_id == row.class_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()
        existing = {
            s.student_id: s
            for s in await self.repo.list_submissions(school_id=school_id, homework_id=homework_id)
        }
        out: list[SubmissionOut] = []
        for enr in enrollments:
            if enr.student_id in existing:
                out.append(await self._to_submission_out(existing[enr.student_id], row))
            else:
                out.append(
                    SubmissionOut(
                        id=None,
                        homework_id=row.id,
                        student_id=enr.student_id,
                        student_name=await self._student_name(enr.student_id),
                        school_id=school_id,
                        content=None,
                        submitted_at=None,
                        status=HomeworkSubmissionStatus.PENDING.value,
                        marks=None,
                        feedback=None,
                        reviewed_by=None,
                        reviewed_at=None,
                        created_at=None,
                        attachments=[],
                        homework_title=row.title,
                        max_marks=row.max_marks,
                    )
                )
        out.sort(key=lambda s: (s.status == HomeworkSubmissionStatus.PENDING.value, s.student_name or ""))
        return out

    async def get_my_submission(
        self, *, school_id: UUID, user_id: UUID, homework_id: UUID
    ) -> SubmissionOut | None:
        student = await self.student_for_user(school_id=school_id, user_id=user_id)
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        enr = await self._active_enrollment(school_id, student.id)
        if not enr or enr.class_id != row.class_id:
            raise ForbiddenError("Homework is not for your section")
        sub = await self.repo.get_submission(
            school_id=school_id, homework_id=homework_id, student_id=student.id
        )
        if not sub:
            return None
        return await self._to_submission_out(sub, row)

    async def submit(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        homework_id: UUID,
        data: SubmissionCreate,
    ) -> SubmissionOut:
        student = await self.student_for_user(school_id=school_id, user_id=user_id)
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        if row.status != HomeworkStatus.PUBLISHED.value:
            raise AppError("Homework is not open for submissions", code="not_open", status_code=409)
        enr = await self._active_enrollment(school_id, student.id)
        if not enr or enr.class_id != row.class_id:
            raise ForbiddenError("Homework is not for your section")

        now = _utcnow()
        status = HomeworkSubmissionStatus.SUBMITTED.value
        if row.due_at:
            due = row.due_at if row.due_at.tzinfo else row.due_at.replace(tzinfo=timezone.utc)
            if now > due:
                status = HomeworkSubmissionStatus.LATE.value

        sub = await self.repo.get_submission(
            school_id=school_id, homework_id=homework_id, student_id=student.id
        )
        if sub:
            if sub.status == HomeworkSubmissionStatus.GRADED.value and not row.allow_resubmission:
                raise ConflictError("Resubmission is not allowed")
            if (
                sub.status
                in (
                    HomeworkSubmissionStatus.SUBMITTED.value,
                    HomeworkSubmissionStatus.LATE.value,
                    HomeworkSubmissionStatus.GRADED.value,
                )
                and not row.allow_resubmission
                and sub.status != HomeworkSubmissionStatus.RETURNED.value
            ):
                raise ConflictError("Already submitted. Resubmission is not allowed.")
            if sub.status == HomeworkSubmissionStatus.RETURNED.value or row.allow_resubmission:
                pass
            elif sub.status in (
                HomeworkSubmissionStatus.SUBMITTED.value,
                HomeworkSubmissionStatus.LATE.value,
            ):
                # allow update if resubmission enabled
                if not row.allow_resubmission:
                    raise ConflictError("Already submitted")
            sub.content = data.content
            sub.submitted_at = now
            sub.status = status
            sub.marks = None if row.allow_resubmission else sub.marks
            sub.feedback = None if status != HomeworkSubmissionStatus.GRADED.value else sub.feedback
            if row.allow_resubmission:
                sub.marks = None
                sub.feedback = None
                sub.reviewed_by = None
                sub.reviewed_at = None
        else:
            if not data.content and True:  # content optional if file attached later
                pass
            sub = HomeworkSubmission(
                school_id=school_id,
                homework_id=homework_id,
                student_id=student.id,
                content=data.content,
                submitted_at=now,
                status=status,
            )
            self.db.add(sub)
            await self.db.flush()

        return await self._to_submission_out(sub, row)

    async def grade(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        submission_id: UUID,
        data: SubmissionGrade,
    ) -> SubmissionOut:
        sub = await self.repo.get_submission_by_id(school_id=school_id, submission_id=submission_id)
        if not sub:
            raise NotFoundError("Submission not found")
        homework = await self.repo.get(school_id, sub.homework_id)
        if not homework:
            raise NotFoundError("Homework not found")
        await self._assert_can_manage(
            school_id=school_id, user_id=user_id, role=role, homework=homework
        )
        if sub.status == HomeworkSubmissionStatus.PENDING.value or not sub.submitted_at:
            raise AppError("Student has not submitted yet", code="not_submitted", status_code=409)

        if data.marks is not None and homework.max_marks is not None and data.marks > homework.max_marks:
            raise AppError(
                f"Marks cannot exceed max ({homework.max_marks})",
                code="marks_overflow",
                status_code=400,
            )

        target = data.status.value if data.status else HomeworkSubmissionStatus.GRADED.value
        sub.marks = data.marks if data.marks is not None else sub.marks
        sub.feedback = data.feedback if data.feedback is not None else sub.feedback
        sub.status = target
        sub.reviewed_by = user_id
        sub.reviewed_at = _utcnow()

        await self._notify_graded(homework, sub)
        await self._recompute_student_progress(
            school_id=school_id,
            student_id=sub.student_id,
            academic_year_id=homework.academic_year_id,
        )
        return await self._to_submission_out(sub, homework)

    # ── attachments ──────────────────────────────────────────

    async def upload_homework_file(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        homework_id: UUID,
        file: UploadFile,
    ) -> AttachmentOut:
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        await self._assert_can_manage(school_id=school_id, user_id=user_id, role=role, homework=row)
        data, name, mime, size = await self._read_upload(file)
        HOMEWORK_DIR.mkdir(parents=True, exist_ok=True)
        key_name = f"{homework_id}_{uuid4().hex[:8]}_{name}"
        path = HOMEWORK_DIR / key_name
        path.write_bytes(data)
        file_key = f"homework/{key_name}"
        att = HomeworkAttachment(
            school_id=school_id,
            homework_id=homework_id,
            file_name=name,
            file_key=file_key,
            mime_type=mime,
            file_size=size,
            uploaded_by=user_id,
        )
        await self.repo.add_homework_attachment(att)
        return self._att_out(att)

    async def upload_submission_file(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        homework_id: UUID,
        file: UploadFile,
    ) -> AttachmentOut:
        # Ensure submission exists (create PENDING then submit if needed)
        student = await self.student_for_user(school_id=school_id, user_id=user_id)
        row = await self.repo.get(school_id, homework_id)
        if not row:
            raise NotFoundError("Homework not found")
        if row.status != HomeworkStatus.PUBLISHED.value:
            raise AppError("Homework is not open", code="not_open", status_code=409)
        enr = await self._active_enrollment(school_id, student.id)
        if not enr or enr.class_id != row.class_id:
            raise ForbiddenError("Homework is not for your section")

        sub = await self.repo.get_submission(
            school_id=school_id, homework_id=homework_id, student_id=student.id
        )
        if not sub:
            # Create submission shell then mark submitted
            await self.submit(
                school_id=school_id,
                user_id=user_id,
                homework_id=homework_id,
                data=SubmissionCreate(content=None),
            )
            sub = await self.repo.get_submission(
                school_id=school_id, homework_id=homework_id, student_id=student.id
            )
        assert sub is not None

        data, name, mime, size = await self._read_upload(file)
        SUBMISSION_DIR.mkdir(parents=True, exist_ok=True)
        key_name = f"{sub.id}_{uuid4().hex[:8]}_{name}"
        path = SUBMISSION_DIR / key_name
        path.write_bytes(data)
        file_key = f"homework-submissions/{key_name}"
        att = SubmissionAttachment(
            school_id=school_id,
            submission_id=sub.id,
            file_name=name,
            file_key=file_key,
            mime_type=mime,
            file_size=size,
        )
        await self.repo.add_submission_attachment(att)
        return self._att_out(att)

    async def _read_upload(self, file: UploadFile) -> tuple[bytes, str, str | None, int]:
        data = await file.read()
        if not data:
            raise ConflictError("Empty upload")
        if len(data) > MAX_FILE_BYTES:
            raise ConflictError("File too large (max 10MB)")
        name = (file.filename or "file").replace("/", "_")[:200]
        return data, name, file.content_type, len(data)

    # ── parent / admin ───────────────────────────────────────

    async def list_children(self, *, school_id: UUID, user_id: UUID) -> list[ChildBriefOut]:
        parent = await self.parent_for_user(school_id=school_id, user_id=user_id)
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent.id,
                )
            )
        ).scalars().all()
        out: list[ChildBriefOut] = []
        for link in links:
            enr = await self._active_enrollment(school_id, link.student_id)
            out.append(
                ChildBriefOut(
                    student_id=link.student_id,
                    full_name=await self._student_name(link.student_id) or "Student",
                    class_id=enr.class_id if enr else None,
                    class_name=await self._class_name(enr.class_id) if enr else None,
                )
            )
        return out

    async def list_for_child(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        student_id: UUID,
        homework_type: str | None = None,
        homework_types: list[str] | None = None,
    ) -> list[HomeworkOut]:
        await self.assert_parent_of(school_id=school_id, user_id=user_id, student_id=student_id)
        enr = await self._active_enrollment(school_id, student_id)
        if not enr:
            return []
        rows = await self.repo.list_for_section(
            school_id=school_id,
            class_id=enr.class_id,
            homework_type=homework_type,
            homework_types=homework_types,
        )
        out: list[HomeworkOut] = []
        for r in rows:
            sub = await self.repo.get_submission(
                school_id=school_id, homework_id=r.id, student_id=student_id
            )
            out.append(await self._to_homework_out(r, my_submission=sub))
            if not sub:
                out[-1].my_submission_status = HomeworkSubmissionStatus.PENDING.value
        return out

    async def reports(
        self,
        *,
        school_id: UUID,
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
        teacher_id: UUID | None = None,
        status: str | None = None,
        academic_year_id: UUID | None = None,
    ) -> list[HomeworkReportOut]:
        rows = await self.repo.list(
            school_id=school_id,
            class_id=class_id,
            subject_id=subject_id,
            teacher_id=teacher_id,
            status=status,
            academic_year_id=academic_year_id,
        )
        out: list[HomeworkReportOut] = []
        for r in rows:
            stats = await self.submission_stats(school_id=school_id, homework_id=r.id)
            out.append(
                HomeworkReportOut(
                    homework_id=r.id,
                    title=r.title,
                    class_name=await self._class_name(r.class_id),
                    subject_name=await self._subject_name(r.subject_id),
                    teacher_name=await self._teacher_name(r.teacher_id),
                    status=r.status,
                    due_at=r.due_at,
                    total_students=stats.total,
                    submitted=stats.submitted,
                    pending=stats.pending,
                    late=stats.late,
                    graded=stats.graded,
                )
            )
        return out

    # ── notifications ────────────────────────────────────────

    async def _notify_published(self, homework: Homework) -> None:
        subject = await self._subject_name(homework.subject_id) or "Subject"
        due = homework.due_at.strftime("%d %b %Y") if homework.due_at else "TBA"
        body = f"New {subject} homework: {homework.title}. Due {due}."
        enrollments = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.school_id == homework.school_id,
                    Enrollment.class_id == homework.class_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()
        notified_users: set[UUID] = set()
        for enr in enrollments:
            student = await self.db.get(Student, enr.student_id)
            if student and student.user_id not in notified_users:
                notified_users.add(student.user_id)
                await create_in_app_notification(
                    self.db,
                    school_id=homework.school_id,
                    user_id=student.user_id,
                    title="New homework",
                    body=body,
                    category="HOMEWORK_PUBLISHED",
                    link_path="/student/homework",
                    meta={"homework_id": str(homework.id)},
                    entity_type="homework",
                    entity_id=homework.id,
                    event_key=f"HOMEWORK_PUBLISHED:{homework.id}:{student.user_id}",
                )
            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == homework.school_id,
                        StudentParent.student_id == enr.student_id,
                    )
                )
            ).scalars().all()
            for link in links:
                parent = await self.db.get(Parent, link.parent_id)
                if not parent or parent.user_id in notified_users:
                    continue
                notified_users.add(parent.user_id)
                name = await self._student_name(enr.student_id) or "Your child"
                await create_in_app_notification(
                    self.db,
                    school_id=homework.school_id,
                    user_id=parent.user_id,
                    title="New homework",
                    body=f"{name}: {body}",
                    category="HOMEWORK_PUBLISHED",
                    link_path="/parent/homework",
                    meta={"homework_id": str(homework.id), "student_id": str(enr.student_id)},
                    entity_type="homework",
                    entity_id=homework.id,
                    event_key=f"HOMEWORK_PUBLISHED:{homework.id}:{parent.user_id}",
                )

    async def _recompute_student_progress(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        academic_year_id: UUID | None = None,
    ) -> None:
        """Best-effort progress refresh after grading — never block the grade flow."""
        try:
            from src.modules.progress.service import ProgressService

            await ProgressService(self.db).recompute(
                school_id=school_id,
                student_id=student_id,
                academic_year_id=academic_year_id,
            )
        except Exception:
            # Grading must succeed even if progress recompute fails.
            return

    async def _notify_graded(self, homework: Homework, sub: HomeworkSubmission) -> None:
        subject = await self._subject_name(homework.subject_id) or "Subject"
        marks_txt = (
            f"{sub.marks}/{homework.max_marks}" if sub.marks is not None and homework.max_marks else str(sub.marks)
        )
        if sub.status == HomeworkSubmissionStatus.RETURNED.value:
            title = "Homework returned"
            body = f"{subject}: {homework.title} was returned for resubmission."
            category = "HOMEWORK_RETURNED"
        else:
            title = "Homework graded"
            body = f"{subject}: {homework.title} — {marks_txt}"
            category = "HOMEWORK_GRADED"

        student = await self.db.get(Student, sub.student_id)
        if student:
            await create_in_app_notification(
                self.db,
                school_id=homework.school_id,
                user_id=student.user_id,
                title=title,
                body=body,
                category=category,
                link_path="/student/homework",
                meta={"homework_id": str(homework.id)},
                entity_type="homework",
                entity_id=homework.id,
                event_key=f"{category}:{homework.id}:{sub.id}:{student.user_id}",
            )
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == homework.school_id,
                    StudentParent.student_id == sub.student_id,
                )
            )
        ).scalars().all()
        name = await self._student_name(sub.student_id) or "Your child"
        for link in links:
            parent = await self.db.get(Parent, link.parent_id)
            if not parent:
                continue
            await create_in_app_notification(
                self.db,
                school_id=homework.school_id,
                user_id=parent.user_id,
                title=title,
                body=f"{name}: {body}",
                category=category,
                link_path="/parent/homework",
                meta={"homework_id": str(homework.id), "student_id": str(sub.student_id)},
                entity_type="homework",
                entity_id=homework.id,
                event_key=f"{category}:{homework.id}:{sub.id}:{parent.user_id}",
            )
