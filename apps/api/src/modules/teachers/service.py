"""Teacher master + Cognito-backed school staff for Phase 4."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Grade,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.schools.provisioning import create_school_user, set_user_active
from src.modules.teachers.schemas import (
    ClassTeacherSectionBrief,
    TeacherAssignmentBrief,
    TeacherCreate,
    TeacherDashboardOut,
    TeacherOut,
    TeacherUpdate,
)
from src.shared.enums import EnrollmentStatus, PersonStatus, UserRole


class TeacherService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: TeacherCreate) -> TeacherOut:
        if data.employee_code:
            await self._assert_unique_employee(school_id, data.employee_code)

        user, _, temp = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=UserRole.TEACHER,
        )
        if data.phone:
            user.phone = data.phone

        teacher_code = await self._next_teacher_code(school_id)
        teacher = Teacher(
            school_id=school_id,
            user_id=user.id,
            teacher_code=teacher_code,
            employee_code=data.employee_code or teacher_code,
            title=data.title,
            phone=data.phone,
            date_of_birth=data.date_of_birth,
            joining_date=data.joining_date or date.today(),
            qualification=data.qualification,
            photo_url=data.photo_url,
            status=(data.status or PersonStatus.ACTIVE.value).upper(),
        )
        self.db.add(teacher)
        await self.db.flush()

        if data.class_id and data.subject_id:
            await self._create_assignment(
                school_id,
                teacher_id=teacher.id,
                class_id=data.class_id,
                subject_id=data.subject_id,
                academic_year_id=data.academic_year_id,
            )

        out = await self.get(school_id, teacher.id)
        out.temporary_password = temp
        return out

    async def list(
        self,
        school_id: UUID,
        *,
        q: str | None = None,
        status: str | None = None,
        academic_year_id: UUID | None = None,
    ) -> list[TeacherOut]:
        base = (
            select(Teacher)
            .where(Teacher.school_id == school_id)
            .join(User, User.id == Teacher.user_id)
        )
        if status:
            su = status.upper()
            if su == PersonStatus.INACTIVE.value:
                base = base.where(
                    or_(Teacher.status == PersonStatus.INACTIVE.value, User.is_active.is_(False))
                )
            elif su == PersonStatus.ACTIVE.value:
                base = base.where(
                    Teacher.status == PersonStatus.ACTIVE.value, User.is_active.is_(True)
                )
            else:
                base = base.where(Teacher.status == su)
        if q:
            like = f"%{q.strip()}%"
            base = base.where(
                or_(
                    User.full_name.ilike(like),
                    User.email.ilike(like),
                    Teacher.employee_code.ilike(like),
                    Teacher.teacher_code.ilike(like),
                    Teacher.phone.ilike(like),
                )
            )
        if academic_year_id:
            base = base.join(
                TeacherAssignment,
                TeacherAssignment.teacher_id == Teacher.id,
            ).where(TeacherAssignment.academic_year_id == academic_year_id)

        rows = list(
            (await self.db.execute(base.order_by(User.full_name.asc()))).scalars().unique().all()
        )
        return [await self._to_out(t) for t in rows]

    async def get(self, school_id: UUID, teacher_id: UUID) -> TeacherOut:
        teacher = await self._get_teacher(school_id, teacher_id)
        return await self._to_out(teacher)

    async def get_me(self, school_id: UUID, user_id: UUID) -> TeacherOut:
        teacher = await self._get_by_user(school_id, user_id)
        return await self._to_out(teacher)

    async def dashboard(self, school_id: UUID, user_id: UUID) -> TeacherDashboardOut:
        teacher_out = await self.get_me(school_id, user_id)
        subjects = sorted(
            {a.subject_name for a in teacher_out.assignments if a.subject_name}
        )
        # Unique classes by class_id (prefer richest subject label for display list)
        by_class: dict[UUID, TeacherAssignmentBrief] = {}
        for a in teacher_out.assignments:
            prev = by_class.get(a.class_id)
            if prev is None or a.student_count > prev.student_count:
                by_class[a.class_id] = a
        total_students = sum(c.student_count for c in by_class.values())
        # Class-teacher sections may overlap — don't double-count unique students across
        # assignments; dashboard total uses unique class counts above.
        return TeacherDashboardOut(
            teacher=teacher_out,
            classes=list(by_class.values()),
            subjects=subjects,
            total_students=total_students,
            class_teacher_sections=teacher_out.class_teacher_of,
        )

    async def update(self, school_id: UUID, teacher_id: UUID, data: TeacherUpdate) -> TeacherOut:
        teacher = await self._get_teacher(school_id, teacher_id)
        user = await self.db.get(User, teacher.user_id)
        if not user:
            raise NotFoundError("User not found")

        payload = data.model_dump(exclude_unset=True)
        if "employee_code" in payload and payload["employee_code"]:
            await self._assert_unique_employee(
                school_id, payload["employee_code"], exclude_id=teacher.id
            )

        if "full_name" in payload and payload["full_name"]:
            user.full_name = payload.pop("full_name").strip()

        if "is_active" in payload:
            active = bool(payload.pop("is_active"))
            await set_user_active(self.db, user=user, active=active)
            if not active and "status" not in payload:
                teacher.status = PersonStatus.INACTIVE.value
            elif active and teacher.status == PersonStatus.INACTIVE.value:
                teacher.status = PersonStatus.ACTIVE.value

        if "status" in payload and payload["status"]:
            teacher.status = payload.pop("status").upper()
            if teacher.status == PersonStatus.INACTIVE.value:
                await set_user_active(self.db, user=user, active=False)
            elif teacher.status == PersonStatus.ACTIVE.value:
                await set_user_active(self.db, user=user, active=True)

        if "phone" in payload:
            phone = payload.pop("phone")
            teacher.phone = phone
            user.phone = phone

        for k, v in payload.items():
            setattr(teacher, k, v)
        await self.db.flush()
        return await self.get(school_id, teacher_id)

    async def set_status(
        self,
        school_id: UUID,
        teacher_id: UUID,
        *,
        status: str | None,
        is_active: bool | None,
    ) -> TeacherOut:
        return await self.update(
            school_id, teacher_id, TeacherUpdate(status=status, is_active=is_active)
        )

    async def upload_photo(
        self, school_id: UUID, teacher_id: UUID, file: UploadFile
    ) -> TeacherOut:
        teacher = await self._get_teacher(school_id, teacher_id)
        content_type = (file.content_type or "").lower()
        ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
        ext = ext_map.get(content_type)
        if not ext:
            raise ConflictError("Photo must be PNG, JPG, or WEBP")
        data = await file.read()
        if not data:
            raise ConflictError("Empty upload")
        if len(data) > 2_000_000:
            raise ConflictError("Photo too large (max 2MB)")

        media_dir = Path(__file__).resolve().parents[3] / "media"
        photo_dir = media_dir / "teacher-photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{teacher.id}{ext}"
        (photo_dir / filename).write_bytes(data)
        teacher.photo_url = f"/media/teacher-photos/{filename}"
        user = await self.db.get(User, teacher.user_id)
        if user:
            user.avatar_url = teacher.photo_url
        await self.db.flush()
        return await self.get(school_id, teacher_id)

    async def assert_teacher_owns_user(self, school_id: UUID, user_id: UUID, teacher_id: UUID) -> None:
        teacher = await self._get_teacher(school_id, teacher_id)
        if teacher.user_id != user_id:
            raise ForbiddenError("Not your teacher profile")

    # ── internals ──────────────────────────────────────────────────────────

    async def _get_teacher(self, school_id: UUID, teacher_id: UUID) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise NotFoundError("Teacher not found")
        return teacher

    async def _get_by_user(self, school_id: UUID, user_id: UUID) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise NotFoundError("Teacher profile not found")
        return teacher

    async def _assert_unique_employee(
        self, school_id: UUID, employee_code: str, exclude_id: UUID | None = None
    ) -> None:
        q = select(Teacher).where(
            Teacher.school_id == school_id, Teacher.employee_code == employee_code
        )
        if exclude_id:
            q = q.where(Teacher.id != exclude_id)
        if (await self.db.execute(q)).scalar_one_or_none():
            raise ConflictError("Employee ID already exists")

    async def _next_teacher_code(self, school_id: UUID) -> str:
        year = date.today().year
        prefix = f"TCH-{year}-"
        lock_key = (school_id.int ^ (year + 17)) & 0x7FFFFFFF
        await self.db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
        result = await self.db.execute(
            select(Teacher.teacher_code)
            .where(Teacher.school_id == school_id, Teacher.teacher_code.like(f"{prefix}%"))
            .order_by(Teacher.teacher_code.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        n = 1
        if last:
            try:
                n = int(str(last).rsplit("-", 1)[-1]) + 1
            except ValueError:
                n = 1
        for _ in range(50):
            code = f"{prefix}{n:04d}"
            clash = (
                await self.db.execute(
                    select(Teacher.id).where(
                        Teacher.school_id == school_id, Teacher.teacher_code == code
                    )
                )
            ).scalar_one_or_none()
            if clash is None:
                return code
            n += 1
        raise ConflictError("Could not allocate teacher ID")

    async def _create_assignment(
        self,
        school_id: UUID,
        *,
        teacher_id: UUID,
        class_id: UUID,
        subject_id: UUID,
        academic_year_id: UUID | None,
    ) -> TeacherAssignment:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Section not found")
        subject = await self.db.get(Subject, subject_id)
        if not subject or subject.school_id != school_id:
            raise NotFoundError("Subject not found")
        year_id = academic_year_id or classroom.academic_year_id
        existing = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.teacher_id == teacher_id,
                    TeacherAssignment.subject_id == subject_id,
                    TeacherAssignment.class_id == class_id,
                    TeacherAssignment.academic_year_id == year_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ConflictError("Assignment already exists")
        row = TeacherAssignment(
            school_id=school_id,
            teacher_id=teacher_id,
            subject_id=subject_id,
            class_id=class_id,
            academic_year_id=year_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def _student_count(self, class_id: UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.class_id == class_id,
                Enrollment.status == EnrollmentStatus.ACTIVE.value,
            )
        )
        return int(result.scalar_one())

    async def _to_out(self, teacher: Teacher) -> TeacherOut:
        user = await self.db.get(User, teacher.user_id)
        rows = (
            await self.db.execute(
                select(TeacherAssignment)
                .where(TeacherAssignment.teacher_id == teacher.id)
                .order_by(TeacherAssignment.created_at.desc())
            )
        ).scalars().all()

        assignments: list[TeacherAssignmentBrief] = []
        for row in rows:
            subject = await self.db.get(Subject, row.subject_id)
            classroom = await self.db.get(ClassRoom, row.class_id)
            year = await self.db.get(AcademicYear, row.academic_year_id) if row.academic_year_id else None
            grade = None
            if classroom and classroom.grade_id:
                grade = await self.db.get(Grade, classroom.grade_id)
            is_ct = bool(classroom and classroom.class_teacher_id == teacher.id)
            assignments.append(
                TeacherAssignmentBrief(
                    id=row.id,
                    subject_id=row.subject_id,
                    subject_name=subject.name if subject else None,
                    class_id=row.class_id,
                    class_name=classroom.name if classroom else None,
                    section=classroom.section if classroom else None,
                    grade_id=classroom.grade_id if classroom else None,
                    grade_name=grade.name if grade else None,
                    academic_year_id=row.academic_year_id,
                    academic_year_name=year.name if year else None,
                    student_count=await self._student_count(row.class_id),
                    is_class_teacher=is_ct,
                )
            )

        class_teacher_rows = (
            await self.db.execute(
                select(ClassRoom).where(
                    ClassRoom.school_id == teacher.school_id,
                    ClassRoom.class_teacher_id == teacher.id,
                )
            )
        ).scalars().all()
        class_teacher_of: list[ClassTeacherSectionBrief] = []
        for classroom in class_teacher_rows:
            year = await self.db.get(AcademicYear, classroom.academic_year_id)
            grade = await self.db.get(Grade, classroom.grade_id) if classroom.grade_id else None
            class_teacher_of.append(
                ClassTeacherSectionBrief(
                    class_id=classroom.id,
                    class_name=classroom.name,
                    section=classroom.section,
                    grade_name=grade.name if grade else None,
                    academic_year_name=year.name if year else None,
                    student_count=await self._student_count(classroom.id),
                )
            )

        return TeacherOut(
            id=teacher.id,
            school_id=teacher.school_id,
            user_id=teacher.user_id,
            teacher_code=teacher.teacher_code,
            employee_code=teacher.employee_code,
            title=teacher.title,
            full_name=user.full_name if user else None,
            email=user.email if user else None,
            phone=teacher.phone or (user.phone if user else None),
            date_of_birth=teacher.date_of_birth,
            joining_date=teacher.joining_date,
            qualification=teacher.qualification,
            photo_url=teacher.photo_url or (user.avatar_url if user else None),
            status=teacher.status,
            is_active=bool(user.is_active) if user else True,
            created_at=teacher.created_at,
            assignments=assignments,
            class_teacher_of=class_teacher_of,
        )
