"""Student master + enrollment orchestration for Phase 3."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Grade,
    Parent,
    Student,
    StudentParent,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.schools.provisioning import create_school_user, set_user_active
from src.modules.students.schemas import (
    BulkImportConfirmOut,
    BulkImportIssue,
    BulkImportRow,
    BulkImportValidateOut,
    EnrollmentBrief,
    ParentLinkInput,
    StudentCreate,
    StudentOut,
    StudentPage,
    StudentParentBrief,
    StudentUpdate,
)
from src.shared.enums import EnrollmentStatus, PersonStatus, UserRole
from src.shared.utils import utcnow


class StudentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: StudentCreate) -> StudentOut:
        full_name = " ".join(
            p for p in (data.first_name, data.middle_name, data.last_name) if p and p.strip()
        )
        if data.admission_no:
            await self._assert_unique_admission(school_id, data.admission_no)

        user, _, temp = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=full_name,
            password=data.password,
            role=UserRole.STUDENT,
            first_name=data.first_name,
            middle_name=data.middle_name,
            last_name=data.last_name,
        )
        if data.phone:
            user.phone = data.phone

        student_code = await self._next_student_code(school_id)
        student = Student(
            school_id=school_id,
            user_id=user.id,
            admission_no=data.admission_no or student_code,
            student_code=student_code,
            date_of_birth=data.date_of_birth,
            gender=data.gender,
            blood_group=data.blood_group,
            nationality=data.nationality,
            phone=data.phone,
            address_line1=data.address_line1,
            address_line2=data.address_line2,
            city=data.city,
            state=data.state,
            postal_code=data.postal_code,
            country=data.country,
            admission_date=data.admission_date or date.today(),
            photo_url=data.photo_url,
            status=(data.status or PersonStatus.ACTIVE.value).upper(),
        )
        self.db.add(student)
        await self.db.flush()

        if data.class_id:
            await self._enroll(
                school_id,
                student.id,
                class_id=data.class_id,
                academic_year_id=data.academic_year_id,
                roll_number=data.roll_number,
            )

        for link in data.parents:
            await self._link_or_create_parent(school_id, student.id, link)

        out = await self.get(school_id, student.id)
        out.temporary_password = temp
        return out

    async def list(
        self,
        school_id: UUID,
        *,
        q: str | None = None,
        academic_year_id: UUID | None = None,
        grade_id: UUID | None = None,
        class_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: str = "name",
        actor_user_id: UUID | None = None,
        actor_role: UserRole | None = None,
    ) -> StudentPage:
        base = (
            select(Student)
            .where(Student.school_id == school_id)
            .join(User, User.id == Student.user_id)
        )

        if status:
            status_u = status.upper()
            if status_u == PersonStatus.INACTIVE.value:
                base = base.where(
                    or_(
                        Student.status == PersonStatus.INACTIVE.value,
                        User.is_active.is_(False),
                    )
                )
            elif status_u == PersonStatus.ACTIVE.value:
                base = base.where(
                    Student.status == PersonStatus.ACTIVE.value,
                    User.is_active.is_(True),
                )
            else:
                base = base.where(Student.status == status_u)

        if q:
            like = f"%{q.strip()}%"
            base = base.where(
                or_(
                    User.full_name.ilike(like),
                    User.email.ilike(like),
                    User.first_name.ilike(like),
                    User.last_name.ilike(like),
                    Student.admission_no.ilike(like),
                    Student.student_code.ilike(like),
                )
            )

        needs_enrollment = bool(
            academic_year_id or grade_id or class_id or actor_role == UserRole.TEACHER
        )
        if needs_enrollment:
            base = base.join(
                Enrollment,
                and_(
                    Enrollment.student_id == Student.id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                ),
            )
            if academic_year_id:
                base = base.where(Enrollment.academic_year_id == academic_year_id)
            if grade_id:
                base = base.where(Enrollment.grade_id == grade_id)
            if class_id:
                base = base.where(Enrollment.class_id == class_id)

        if actor_role == UserRole.TEACHER and actor_user_id:
            teacher = (
                await self.db.execute(
                    select(Teacher).where(
                        Teacher.school_id == school_id, Teacher.user_id == actor_user_id
                    )
                )
            ).scalar_one_or_none()
            if not teacher:
                return StudentPage(items=[], total=0, page=page, page_size=page_size)
            assigned = select(TeacherAssignment.class_id).where(
                TeacherAssignment.teacher_id == teacher.id
            )
            class_teacher_sections = select(ClassRoom.id).where(
                ClassRoom.school_id == school_id, ClassRoom.class_teacher_id == teacher.id
            )
            base = base.where(
                or_(
                    Enrollment.class_id.in_(assigned),
                    Enrollment.class_id.in_(class_teacher_sections),
                )
            )

        count_q = select(func.count()).select_from(base.order_by(None).subquery())
        total = int((await self.db.execute(count_q)).scalar_one())

        if sort == "created":
            base = base.order_by(Student.created_at.desc())
        elif sort == "admission":
            base = base.order_by(Student.admission_no.asc().nullslast())
        else:
            base = base.order_by(User.full_name.asc())

        offset = (max(page, 1) - 1) * page_size
        rows = list(
            (await self.db.execute(base.offset(offset).limit(page_size))).scalars().unique().all()
        )
        items = [await self._to_out(s, include_history=False) for s in rows]
        return StudentPage(items=items, total=total, page=page, page_size=page_size)

    async def get(self, school_id: UUID, student_id: UUID) -> StudentOut:
        student = await self._get_student(school_id, student_id)
        return await self._to_out(student, include_history=True)

    async def update(self, school_id: UUID, student_id: UUID, data: StudentUpdate) -> StudentOut:
        student = await self._get_student(school_id, student_id)
        user = await self.db.get(User, student.user_id)
        if not user:
            raise NotFoundError("User not found")

        payload = data.model_dump(exclude_unset=True)
        if "admission_no" in payload and payload["admission_no"]:
            await self._assert_unique_admission(school_id, payload["admission_no"], exclude_id=student.id)

        name_changed = False
        for field in ("first_name", "middle_name", "last_name"):
            if field in payload:
                setattr(user, field, payload.pop(field))
                name_changed = True
        if name_changed:
            parts = [p for p in (user.first_name, user.middle_name, user.last_name) if p]
            user.full_name = " ".join(parts) if parts else user.full_name

        if "is_active" in payload:
            active = bool(payload.pop("is_active"))
            await set_user_active(self.db, user=user, active=active)
            if not active and "status" not in payload:
                student.status = PersonStatus.INACTIVE.value
            elif active and student.status == PersonStatus.INACTIVE.value:
                student.status = PersonStatus.ACTIVE.value

        if "status" in payload and payload["status"]:
            student.status = payload.pop("status").upper()
            if student.status == PersonStatus.INACTIVE.value:
                await set_user_active(self.db, user=user, active=False)
            elif student.status == PersonStatus.ACTIVE.value:
                await set_user_active(self.db, user=user, active=True)

        for k, v in payload.items():
            setattr(student, k, v)
        await self.db.flush()
        return await self.get(school_id, student_id)

    async def set_status(
        self, school_id: UUID, student_id: UUID, *, status: str | None, is_active: bool | None
    ) -> StudentOut:
        return await self.update(
            school_id,
            student_id,
            StudentUpdate(status=status, is_active=is_active),
        )

    async def enroll(
        self,
        school_id: UUID,
        student_id: UUID,
        *,
        class_id: UUID,
        academic_year_id: UUID | None = None,
        roll_number: str | None = None,
    ) -> StudentOut:
        await self._get_student(school_id, student_id)
        await self._enroll(
            school_id,
            student_id,
            class_id=class_id,
            academic_year_id=academic_year_id,
            roll_number=roll_number,
        )
        return await self.get(school_id, student_id)

    async def transfer(
        self,
        school_id: UUID,
        student_id: UUID,
        *,
        class_id: UUID,
        roll_number: str | None = None,
    ) -> StudentOut:
        """Transfer section/grade within the same academic year.

        Archives the ACTIVE enrollment as TRANSFERRED and creates a new ACTIVE
        row so history stays accurate. Cross-year placement must use enroll().
        """
        student = await self._get_student(school_id, student_id)
        classroom = await self._get_classroom(school_id, class_id)
        active = await self._active_enrollment(student.id)
        if not active:
            raise ConflictError("Student has no active enrollment to transfer")
        if classroom.academic_year_id != active.academic_year_id:
            raise ConflictError(
                "Target section is in a different academic year — use Enroll for the new year"
            )
        if classroom.id == active.class_id and (
            roll_number is None or roll_number == active.roll_number
        ):
            return await self.get(school_id, student_id)

        await self._assert_roll_available(
            classroom.id, roll_number if roll_number is not None else active.roll_number
        )

        old_class = await self.db.get(ClassRoom, active.class_id)
        old_label = old_class.name if old_class else str(active.class_id)
        note = f"Transferred {old_label} → {classroom.name} on {utcnow().date().isoformat()}"
        active.status = EnrollmentStatus.TRANSFERRED.value
        active.left_at = utcnow()
        active.notes = f"{(active.notes or '').strip()}\n{note}".strip()

        new_roll = roll_number if roll_number is not None else active.roll_number
        row = Enrollment(
                    school_id=school_id,
                    student_id=student.id,
            class_id=classroom.id,
            academic_year_id=classroom.academic_year_id,
            grade_id=classroom.grade_id,
            roll_number=new_roll,
            status=EnrollmentStatus.ACTIVE.value,
            notes=note,
        )
        self.db.add(row)
        await self.db.flush()
        return await self.get(school_id, student_id)

    async def withdraw(self, school_id: UUID, student_id: UUID, *, notes: str | None = None) -> StudentOut:
        student = await self._get_student(school_id, student_id)
        active = await self._active_enrollment(student.id)
        if active:
            active.status = EnrollmentStatus.WITHDRAWN.value
            active.left_at = utcnow()
            if notes:
                active.notes = ((active.notes or "") + "\n" + notes).strip()
        student.status = PersonStatus.LEFT.value
        user = await self.db.get(User, student.user_id)
        if user:
            await set_user_active(self.db, user=user, active=False)
        await self.db.flush()
        return await self.get(school_id, student_id)

    async def validate_import(self, school_id: UUID, rows: list[BulkImportRow]) -> BulkImportValidateOut:
        issues: list[BulkImportIssue] = []
        years = {
            y.name: y
            for y in (
                await self.db.execute(select(AcademicYear).where(AcademicYear.school_id == school_id))
            ).scalars().all()
        }
        grades = {
            g.name.lower(): g
            for g in (await self.db.execute(select(Grade).where(Grade.school_id == school_id))).scalars().all()
        }
        sections = list(
            (await self.db.execute(select(ClassRoom).where(ClassRoom.school_id == school_id))).scalars().all()
        )

        seen_emails: set[str] = set()
        seen_adm: set[str] = set()
        for row in rows:
            email = row.email.lower()
            if email in seen_emails:
                issues.append(BulkImportIssue(row=row.row, field="email", message="Duplicate email in file"))
            seen_emails.add(email)
            existing_user = (
                await self.db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if existing_user:
                already_student = (
                    await self.db.execute(
                        select(Student.id).where(
                            Student.school_id == school_id, Student.user_id == existing_user.id
                        )
                    )
                ).scalar_one_or_none()
                issues.append(
                    BulkImportIssue(
                        row=row.row,
                        field="email",
                        message=(
                            "Email already registered as a student"
                            if already_student
                            else "Email already registered — use a unique student login email"
                        ),
                        level="error",
                    )
                )
            if not row.first_name or not row.last_name:
                issues.append(BulkImportIssue(row=row.row, field="name", message="First and last name required"))
            if not row.email or "invalid.import" in row.email.lower() or row.email.startswith("missing-email"):
                issues.append(BulkImportIssue(row=row.row, field="email", message="Email is required"))
            if not row.date_of_birth:
                issues.append(BulkImportIssue(row=row.row, field="date_of_birth", message="Missing Date of Birth"))
            if row.admission_no:
                if row.admission_no in seen_adm:
                    issues.append(
                        BulkImportIssue(row=row.row, field="admission_no", message="Duplicate Admission Number in file")
                    )
                seen_adm.add(row.admission_no)
                clash = (
                    await self.db.execute(
                        select(Student).where(
                            Student.school_id == school_id, Student.admission_no == row.admission_no
                        )
                    )
                ).scalar_one_or_none()
                if clash:
                    issues.append(
                        BulkImportIssue(
                            row=row.row, field="admission_no", message="Duplicate Admission Number"
                        )
                    )
            if row.academic_year and row.academic_year not in years:
                issues.append(
                    BulkImportIssue(row=row.row, field="academic_year", message="Academic year not found")
                )
            if row.grade and row.grade.lower() not in grades and not any(
                g.name.lower().replace("class ", "") == row.grade.lower().replace("class ", "")
                for g in grades.values()
            ):
                issues.append(BulkImportIssue(row=row.row, field="grade", message="Grade does not exist"))
            if row.section and row.grade:
                matched = self._find_section(sections, years, grades, row)
                if matched is None:
                    issues.append(BulkImportIssue(row=row.row, field="section", message="Section does not exist"))

        error_count = sum(1 for i in issues if i.level == "error")
        valid_count = len(rows) - len({i.row for i in issues if i.level == "error"})
        return BulkImportValidateOut(
            valid_count=max(valid_count, 0),
            error_count=error_count,
            issues=issues,
            rows=rows,
        )

    async def confirm_import(self, school_id: UUID, rows: list[BulkImportRow]) -> BulkImportConfirmOut:
        validation = await self.validate_import(school_id, rows)
        error_rows = {i.row for i in validation.issues if i.level == "error"}
        years = {
            y.name: y
            for y in (
                await self.db.execute(select(AcademicYear).where(AcademicYear.school_id == school_id))
            ).scalars().all()
        }
        grades = {
            g.name.lower(): g
            for g in (await self.db.execute(select(Grade).where(Grade.school_id == school_id))).scalars().all()
        }
        sections = list(
            (await self.db.execute(select(ClassRoom).where(ClassRoom.school_id == school_id))).scalars().all()
        )

        created = 0
        skipped = 0
        errors: list[BulkImportIssue] = []
        students: list[StudentOut] = []
        for row in rows:
            if row.row in error_rows:
                skipped += 1
                continue
            try:
                classroom = self._find_section(sections, years, grades, row)
                parents: list[ParentLinkInput] = []
                if row.parent_email and row.parent_name:
                    parents.append(
                        ParentLinkInput(
                            full_name=row.parent_name,
                            email=row.parent_email,
                            phone=row.parent_phone,
                            relationship=row.relationship or "guardian",
                            is_primary=True,
                        )
                    )
                out = await self.create(
                    school_id,
                    StudentCreate(
                        first_name=row.first_name,
                        middle_name=row.middle_name,
                        last_name=row.last_name,
                        email=row.email,
                        admission_no=row.admission_no,
                        date_of_birth=row.date_of_birth,
                        gender=row.gender,
                        class_id=classroom.id if classroom else None,
                        academic_year_id=classroom.academic_year_id if classroom else None,
                        roll_number=row.roll_number,
                        parents=parents,
                    ),
                )
                students.append(out)
                created += 1
            except Exception as exc:  # noqa: BLE001
                skipped += 1
                errors.append(BulkImportIssue(row=row.row, message=str(exc)))
        return BulkImportConfirmOut(created=created, skipped=skipped, errors=errors, students=students)

    # ── internals ──────────────────────────────────────────────────────────

    async def _get_student(self, school_id: UUID, student_id: UUID) -> Student:
        student = (
            await self.db.execute(
                select(Student).where(Student.id == student_id, Student.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not student:
            raise NotFoundError("Student not found")
        return student

    async def _get_classroom(self, school_id: UUID, class_id: UUID) -> ClassRoom:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Section not found")
        return classroom

    async def _active_enrollment(self, student_id: UUID) -> Enrollment | None:
        return (
            await self.db.execute(
                select(Enrollment)
                .where(
                    Enrollment.student_id == student_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .order_by(Enrollment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _assert_unique_admission(
        self, school_id: UUID, admission_no: str, exclude_id: UUID | None = None
    ) -> None:
        q = select(Student).where(Student.school_id == school_id, Student.admission_no == admission_no)
        if exclude_id:
            q = q.where(Student.id != exclude_id)
        if (await self.db.execute(q)).scalar_one_or_none():
            raise ConflictError("Admission number already exists")

    async def _next_student_code(self, school_id: UUID) -> str:
        """Allocate STU-{year}-{nnnn} under a transaction advisory lock."""
        year = date.today().year
        prefix = f"STU-{year}-"
        lock_key = (school_id.int ^ year) & 0x7FFFFFFF
        await self.db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
        result = await self.db.execute(
            select(Student.student_code)
            .where(
                Student.school_id == school_id,
                Student.student_code.like(f"{prefix}%"),
            )
            .order_by(Student.student_code.desc())
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
                    select(Student.id).where(
                        Student.school_id == school_id, Student.student_code == code
                    )
                )
            ).scalar_one_or_none()
            if clash is None:
                return code
            n += 1
        raise ConflictError("Could not allocate student ID")

    async def _assert_roll_available(
        self, class_id: UUID, roll_number: str | None, *, exclude_enrollment_id: UUID | None = None
    ) -> None:
        if not roll_number or not str(roll_number).strip():
            return
        q = select(Enrollment).where(
            Enrollment.class_id == class_id,
            Enrollment.roll_number == str(roll_number).strip(),
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
        )
        if exclude_enrollment_id:
            q = q.where(Enrollment.id != exclude_enrollment_id)
        if (await self.db.execute(q)).scalar_one_or_none():
            raise ConflictError(f"Roll number {roll_number} is already used in this section")

    async def upload_photo(
        self, school_id: UUID, student_id: UUID, file: UploadFile
    ) -> StudentOut:
        student = await self._get_student(school_id, student_id)
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
        photo_dir = media_dir / "student-photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{student.id}{ext}"
        (photo_dir / filename).write_bytes(data)
        student.photo_url = f"/media/student-photos/{filename}"
        user = await self.db.get(User, student.user_id)
        if user:
            user.avatar_url = student.photo_url
        await self.db.flush()
        return await self.get(school_id, student_id)

    async def assert_teacher_can_view(
        self, school_id: UUID, student_id: UUID, actor_user_id: UUID
    ) -> None:
        teacher = (
            await self.db.execute(
                select(Teacher).where(
                    Teacher.school_id == school_id, Teacher.user_id == actor_user_id
                )
            )
        ).scalar_one_or_none()
        if not teacher:
            raise ForbiddenError("Teacher profile not found")
        enrollment = await self._active_enrollment(student_id)
        if not enrollment:
            raise ForbiddenError("Student is not in your classes")
        assigned = (
            await self.db.execute(
                select(TeacherAssignment.id).where(
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == enrollment.class_id,
                )
            )
        ).scalar_one_or_none()
        if assigned:
            return
        classroom = await self.db.get(ClassRoom, enrollment.class_id)
        if classroom and classroom.class_teacher_id == teacher.id:
            return
        raise ForbiddenError("Student is not in your classes")

    async def _enroll(
        self,
        school_id: UUID,
        student_id: UUID,
        *,
        class_id: UUID,
        academic_year_id: UUID | None,
        roll_number: str | None,
    ) -> Enrollment:
        classroom = await self._get_classroom(school_id, class_id)
        year_id = academic_year_id or classroom.academic_year_id
        await self._assert_roll_available(classroom.id, roll_number)
        existing = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.student_id == student_id,
                    Enrollment.academic_year_id == year_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ConflictError("Student already enrolled for this academic year")

        # Close any other active enrollment (prior year)
        active = await self._active_enrollment(student_id)
        if active and active.academic_year_id != year_id:
            active.status = EnrollmentStatus.COMPLETED.value
            active.left_at = utcnow()

        # Reactivate same-year withdrawn/transferred by creating a fresh ACTIVE row
        row = Enrollment(
            school_id=school_id,
            student_id=student_id,
            class_id=classroom.id,
            academic_year_id=year_id,
            grade_id=classroom.grade_id,
            roll_number=roll_number,
            status=EnrollmentStatus.ACTIVE.value,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def _link_or_create_parent(
        self, school_id: UUID, student_id: UUID, link: ParentLinkInput
    ) -> None:
        parent: Parent | None = None
        if link.parent_id:
            parent = (
                await self.db.execute(
                    select(Parent).where(Parent.id == link.parent_id, Parent.school_id == school_id)
                )
            ).scalar_one_or_none()
            if not parent:
                raise NotFoundError("Parent not found")
        elif link.email and link.full_name:
            user, _, _ = await create_school_user(
                self.db,
                school_id=school_id,
                email=link.email,
                full_name=link.full_name,
                password=link.password,
                role=UserRole.PARENT,
            )
            parent = (
                await self.db.execute(
                    select(Parent).where(Parent.school_id == school_id, Parent.user_id == user.id)
                )
            ).scalar_one_or_none()
            if not parent:
                parent = Parent(school_id=school_id, user_id=user.id, phone=link.phone)
                self.db.add(parent)
                await self.db.flush()
            elif link.phone:
                parent.phone = link.phone
        else:
            return

        existing = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.student_id == student_id, StudentParent.parent_id == parent.id
                )
            )
        ).scalar_one_or_none()
        if link.is_primary:
            await self.db.execute(
                update(StudentParent)
                .where(
                    StudentParent.student_id == student_id,
                    StudentParent.is_primary.is_(True),
                )
                .values(is_primary=False)
            )
        if existing:
            existing.relation_type = link.relationship
            existing.is_primary = link.is_primary
            await self.db.flush()
            return
        self.db.add(
            StudentParent(
                school_id=school_id,
                student_id=student_id,
                parent_id=parent.id,
                relation_type=link.relationship,
                is_primary=link.is_primary,
            )
        )
        await self.db.flush()

    def _find_section(
        self,
        sections: list[ClassRoom],
        years: dict[str, AcademicYear],
        grades: dict[str, Grade],
        row: BulkImportRow,
    ) -> ClassRoom | None:
        year = years.get(row.academic_year or "")
        grade = None
        if row.grade:
            key = row.grade.lower()
            grade = grades.get(key)
            if not grade:
                for g in grades.values():
                    if g.name.lower().replace("class ", "") == key.replace("class ", ""):
                        grade = g
                        break
        for s in sections:
            if row.section and (s.section or "").upper() != row.section.strip().upper():
                # also allow name ending
                if not s.name.upper().endswith(f"-{row.section.strip().upper()}"):
                    continue
            if year and s.academic_year_id != year.id:
                continue
            if grade and s.grade_id != grade.id:
                continue
            if year or grade or row.section:
                return s
        return None

    async def _to_out(self, student: Student, *, include_history: bool) -> StudentOut:
        user = await self.db.get(User, student.user_id)
        enrollment = await self._active_enrollment(student.id)

        class_id = class_name = section = grade_id = grade_name = None
        academic_year_id = academic_year_name = roll_number = enrollment_id = None
        if enrollment:
            enrollment_id = enrollment.id
            class_id = enrollment.class_id
            academic_year_id = enrollment.academic_year_id
            grade_id = enrollment.grade_id
            roll_number = enrollment.roll_number
            classroom = await self.db.get(ClassRoom, enrollment.class_id)
            if classroom:
                class_name = classroom.name
                section = classroom.section
            if enrollment.grade_id:
                grade = await self.db.get(Grade, enrollment.grade_id)
                grade_name = grade.name if grade else None
            year = await self.db.get(AcademicYear, enrollment.academic_year_id)
            academic_year_name = year.name if year else None

        links = (
            await self.db.execute(select(StudentParent).where(StudentParent.student_id == student.id))
        ).scalars().all()
        parents: list[StudentParentBrief] = []
        for link in links:
            parent = await self.db.get(Parent, link.parent_id)
            if not parent:
                continue
            puser = await self.db.get(User, parent.user_id)
            parents.append(
                StudentParentBrief(
                    id=parent.id,
                    user_id=parent.user_id,
                    full_name=puser.full_name if puser else None,
                    email=puser.email if puser else None,
                    relationship=link.relation_type,
                    phone=parent.phone,
                    is_primary=bool(link.is_primary),
                )
            )

        history: list[EnrollmentBrief] = []
        if include_history:
            rows = (
                await self.db.execute(
                    select(Enrollment)
                    .where(Enrollment.student_id == student.id)
                    .order_by(Enrollment.enrolled_at.desc())
                )
            ).scalars().all()
            for row in rows:
                classroom = await self.db.get(ClassRoom, row.class_id)
                grade = await self.db.get(Grade, row.grade_id) if row.grade_id else None
                year = await self.db.get(AcademicYear, row.academic_year_id)
                history.append(
                    EnrollmentBrief(
                        id=row.id,
                        academic_year_id=row.academic_year_id,
                        academic_year_name=year.name if year else None,
                        grade_id=row.grade_id,
                        grade_name=grade.name if grade else None,
                        class_id=row.class_id,
                        class_name=classroom.name if classroom else None,
                        section=classroom.section if classroom else None,
                        roll_number=row.roll_number,
                        status=row.status,
                        enrolled_at=row.enrolled_at,
                        left_at=row.left_at,
                    )
                )

        return StudentOut(
            id=student.id,
            school_id=student.school_id,
            user_id=student.user_id,
            student_code=student.student_code,
            admission_no=student.admission_no,
            first_name=user.first_name if user else None,
            middle_name=user.middle_name if user else None,
            last_name=user.last_name if user else None,
            full_name=user.full_name if user else None,
            email=user.email if user else None,
            date_of_birth=student.date_of_birth,
            gender=student.gender,
            blood_group=student.blood_group,
            nationality=student.nationality,
            phone=student.phone or (user.phone if user else None),
            address_line1=student.address_line1,
            address_line2=student.address_line2,
            city=student.city,
            state=student.state,
            postal_code=student.postal_code,
            country=student.country,
            admission_date=student.admission_date,
            photo_url=student.photo_url or (user.avatar_url if user else None),
            status=student.status,
            is_active=bool(user.is_active) if user else True,
            created_at=student.created_at,
            class_id=class_id,
            class_name=class_name,
            section=section,
            grade_id=grade_id,
            grade_name=grade_name,
            academic_year_id=academic_year_id,
            academic_year_name=academic_year_name,
            roll_number=roll_number,
            enrollment_id=enrollment_id,
            parents=parents,
            enrollments=history,
        )
