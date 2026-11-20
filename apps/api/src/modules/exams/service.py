from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    ClassRoom,
    Enrollment,
    Exam,
    ExamSchedule,
    ExamSection,
    ExamSubject,
    ExamType,
    Grade,
    GradeRule,
    GradingScale,
    MarksEntry,
    Parent,
    Result,
    ResultAuditLog,
    School,
    Student,
    StudentMark,
    StudentParent,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.exams.schemas import (
    AuditLogOut,
    CalculateResultsOut,
    ChildBriefOut,
    ExamCreate,
    ExamOut,
    ExamOverviewOut,
    ExamOverviewSection,
    ExamScheduleCreate,
    ExamScheduleOut,
    ExamSectionOut,
    ExamSubjectCreate,
    ExamSubjectOut,
    ExamSubjectUpdate,
    ExamTypeCreate,
    StudentExamScheduleItemOut,
    ExamTypeOut,
    ExamTypeUpdate,
    ExamUpdate,
    GradeRuleIn,
    GradeRuleOut,
    GradingScaleCreate,
    GradingScaleOut,
    GradingScaleUpdate,
    MarkRowOut,
    MarksCorrectionIn,
    MarksSaveIn,
    MarksSheetOut,
    PublishOut,
    PublishResultsIn,
    StudentResultOut,
    SubjectResultOut,
    TeacherExamOut,
    UnlockMarksIn,
)
from src.modules.notifications.service import create_in_app_notification
from src.shared.enums import (
    EnrollmentStatus,
    ExamAttendanceStatus,
    ExamStatus,
    MarkEntryStatus,
    UserRole,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExamService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── helpers ──────────────────────────────────────────────

    async def _user_name(self, user_id: UUID | None) -> str | None:
        if not user_id:
            return None
        user = await self.db.get(User, user_id)
        return user.full_name if user else None

    async def _student_name(self, student_id: UUID) -> str | None:
        student = await self.db.get(Student, student_id)
        return await self._user_name(student.user_id) if student else None

    async def _class_label(self, class_id: UUID | None) -> str | None:
        if not class_id:
            return None
        room = await self.db.get(ClassRoom, class_id)
        if not room:
            return None
        grade = await self.db.get(Grade, room.grade_id) if room.grade_id else None
        base = grade.name if grade else room.name
        return f"{base}-{room.section}" if room.section else base

    async def _subject_name(self, subject_id: UUID | None) -> str | None:
        if not subject_id:
            return None
        sub = await self.db.get(Subject, subject_id)
        return sub.name if sub else None

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

    async def list_my_schedule(self, school_id: UUID, user_id: UUID) -> list[StudentExamScheduleItemOut]:
        """Upcoming exam schedule for the student's active enrollment class."""
        st = await self._student_for_user(school_id, user_id)
        enr = (
            await self.db.execute(
                select(Enrollment)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id == st.id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .order_by(Enrollment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not enr or not enr.class_id:
            return []
        return await self._schedule_for_enrollment(school_id, enr)

    async def list_child_schedule(
        self, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> list[StudentExamScheduleItemOut]:
        """Exam schedule for a linked child (parent portal)."""
        parent = await self._parent_for_user(school_id, user_id)
        await self._assert_parent_child(school_id, parent.id, student_id)
        enr = (
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
        if not enr or not enr.class_id:
            return []
        return await self._schedule_for_enrollment(school_id, enr)

    async def _schedule_for_enrollment(
        self, school_id: UUID, enr: Enrollment
    ) -> list[StudentExamScheduleItemOut]:
        rows = (
            await self.db.execute(
                select(ExamSchedule, Exam, ExamSubject)
                .join(Exam, Exam.id == ExamSchedule.exam_id)
                .join(ExamSubject, ExamSubject.id == ExamSchedule.exam_subject_id)
                .where(
                    Exam.school_id == school_id,
                    ExamSchedule.class_id == enr.class_id,
                    Exam.academic_year_id == enr.academic_year_id,
                    Exam.status != ExamStatus.DRAFT.value,
                )
                .order_by(ExamSchedule.exam_date.asc(), ExamSchedule.starts_at.asc())
            )
        ).all()

        type_cache: dict[UUID, str | None] = {}
        out: list[StudentExamScheduleItemOut] = []
        class_name = await self._class_label(enr.class_id)
        for sch, exam, es in rows:
            type_name = None
            if exam.exam_type_id:
                if exam.exam_type_id not in type_cache:
                    et = await self.db.get(ExamType, exam.exam_type_id)
                    type_cache[exam.exam_type_id] = et.name if et else None
                type_name = type_cache.get(exam.exam_type_id)
            out.append(
                StudentExamScheduleItemOut(
                    exam_id=exam.id,
                    exam_title=exam.title,
                    exam_type_name=type_name,
                    subject_id=es.subject_id,
                    subject_name=await self._subject_name(es.subject_id),
                    class_id=sch.class_id,
                    class_name=class_name,
                    exam_date=sch.exam_date,
                    starts_at=sch.starts_at,
                    ends_at=sch.ends_at,
                    room=sch.room,
                    duration_minutes=es.duration_minutes,
                )
            )
        return out

    async def _parent_for_user(self, school_id: UUID, user_id: UUID) -> Parent:
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        return parent

    async def _assert_parent_child(self, school_id: UUID, parent_id: UUID, student_id: UUID) -> None:
        link = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent_id,
                    StudentParent.student_id == student_id,
                )
            )
        ).scalar_one_or_none()
        if not link:
            raise ForbiddenError("Not linked to this student")

    async def _get_exam(self, school_id: UUID, exam_id: UUID) -> Exam:
        exam = await self.db.get(Exam, exam_id)
        if not exam or exam.school_id != school_id:
            raise NotFoundError("Exam not found")
        return exam

    async def _assert_teacher_assignment(
        self, school_id: UUID, teacher_id: UUID, class_id: UUID, subject_id: UUID, year_id: UUID | None
    ) -> None:
        q = select(TeacherAssignment).where(
            TeacherAssignment.school_id == school_id,
            TeacherAssignment.teacher_id == teacher_id,
            TeacherAssignment.class_id == class_id,
            TeacherAssignment.subject_id == subject_id,
        )
        if year_id:
            q = q.where(
                (TeacherAssignment.academic_year_id == year_id)
                | (TeacherAssignment.academic_year_id.is_(None))
            )
        row = (await self.db.execute(q.limit(1))).scalar_one_or_none()
        if not row:
            raise ForbiddenError("Not assigned to this class/subject")

    def _grade_for_percentage(self, rules: list[GradeRule], percentage: float | None) -> str | None:
        if percentage is None or not rules:
            return None
        for rule in sorted(rules, key=lambda r: r.min_percentage, reverse=True):
            if rule.min_percentage <= percentage <= rule.max_percentage:
                return rule.grade
        return None

    async def _load_scale(self, school_id: UUID, scale_id: UUID | None) -> GradingScale | None:
        if scale_id:
            scale = await self.db.get(GradingScale, scale_id)
            if scale and scale.school_id == school_id:
                await self.db.refresh(scale, attribute_names=["rules"])
                return scale
        scale = (
            await self.db.execute(
                select(GradingScale)
                .where(GradingScale.school_id == school_id, GradingScale.is_default.is_(True))
                .options(selectinload(GradingScale.rules))
                .limit(1)
            )
        ).scalar_one_or_none()
        return scale

    async def _to_exam_out(self, exam: Exam) -> ExamOut:
        sections = (
            await self.db.execute(select(ExamSection).where(ExamSection.exam_id == exam.id))
        ).scalars().all()
        subjects = (
            await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam.id))
        ).scalars().all()
        schedules = (
            await self.db.execute(select(ExamSchedule).where(ExamSchedule.exam_id == exam.id))
        ).scalars().all()

        type_name = None
        if exam.exam_type_id:
            et = await self.db.get(ExamType, exam.exam_type_id)
            type_name = et.name if et else None
        grade_name = None
        if exam.grade_id:
            g = await self.db.get(Grade, exam.grade_id)
            grade_name = g.name if g else None

        section_outs: list[ExamSectionOut] = []
        for s in sections:
            room = await self.db.get(ClassRoom, s.class_id)
            section_outs.append(
                ExamSectionOut(
                    class_id=s.class_id,
                    class_name=await self._class_label(s.class_id),
                    section=room.section if room else None,
                )
            )

        subject_outs: list[ExamSubjectOut] = []
        for es in subjects:
            subject_outs.append(
                ExamSubjectOut(
                    id=es.id,
                    exam_id=es.exam_id,
                    subject_id=es.subject_id,
                    subject_name=await self._subject_name(es.subject_id),
                    grade_id=es.grade_id,
                    max_marks=es.max_marks,
                    pass_marks=es.pass_marks,
                    duration_minutes=es.duration_minutes,
                    instructions=getattr(es, "instructions", None),
                    exam_date=es.exam_date,
                    starts_at=es.starts_at,
                    ends_at=es.ends_at,
                )
            )

        schedule_outs: list[ExamScheduleOut] = []
        subj_map = {es.id: es for es in subjects}
        for sch in schedules:
            es = subj_map.get(sch.exam_subject_id)
            schedule_outs.append(
                ExamScheduleOut(
                    id=sch.id,
                    exam_id=sch.exam_id,
                    exam_subject_id=sch.exam_subject_id,
                    subject_id=es.subject_id if es else None,
                    subject_name=await self._subject_name(es.subject_id) if es else None,
                    class_id=sch.class_id,
                    exam_date=sch.exam_date,
                    starts_at=sch.starts_at,
                    ends_at=sch.ends_at,
                    room=sch.room,
                )
            )

        return ExamOut(
            id=exam.id,
            school_id=exam.school_id,
            academic_year_id=exam.academic_year_id,
            exam_type_id=exam.exam_type_id,
            exam_type_name=type_name,
            grade_id=exam.grade_id,
            grade_name=grade_name,
            grading_scale_id=exam.grading_scale_id,
            title=exam.title,
            description=exam.description,
            starts_on=exam.starts_on,
            ends_on=exam.ends_on,
            status=exam.status,
            must_pass_all_subjects=exam.must_pass_all_subjects,
            published_at=exam.published_at,
            locked=exam.locked,
            sections=section_outs,
            subjects=subject_outs,
            schedules=schedule_outs,
        )

    # ── exam types ───────────────────────────────────────────

    async def list_exam_types(self, school_id: UUID, active_only: bool = False) -> list[ExamTypeOut]:
        q = select(ExamType).where(ExamType.school_id == school_id).order_by(ExamType.name)
        if active_only:
            q = q.where(ExamType.is_active.is_(True))
        rows = (await self.db.execute(q)).scalars().all()
        return [ExamTypeOut.model_validate(r) for r in rows]

    async def create_exam_type(self, school_id: UUID, data: ExamTypeCreate) -> ExamTypeOut:
        row = ExamType(
            school_id=school_id,
            name=data.name.strip(),
            description=data.description,
            is_active=data.is_active,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return ExamTypeOut.model_validate(row)

    async def update_exam_type(
        self, school_id: UUID, type_id: UUID, data: ExamTypeUpdate
    ) -> ExamTypeOut:
        row = await self.db.get(ExamType, type_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Exam type not found")
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(row, k, v.strip() if k == "name" and isinstance(v, str) else v)
        await self.db.commit()
        await self.db.refresh(row)
        return ExamTypeOut.model_validate(row)

    # ── grading scales ───────────────────────────────────────

    async def list_grading_scales(self, school_id: UUID) -> list[GradingScaleOut]:
        rows = (
            await self.db.execute(
                select(GradingScale)
                .where(GradingScale.school_id == school_id)
                .options(selectinload(GradingScale.rules))
                .order_by(GradingScale.name)
            )
        ).scalars().all()
        return [
            GradingScaleOut(
                id=r.id,
                school_id=r.school_id,
                name=r.name,
                is_default=r.is_default,
                is_active=getattr(r, "is_active", True),
                must_pass_all_subjects=r.must_pass_all_subjects,
                rules=[GradeRuleOut.model_validate(rule) for rule in sorted(r.rules, key=lambda x: x.sort_order)],
            )
            for r in rows
        ]

    async def create_grading_scale(self, school_id: UUID, data: GradingScaleCreate) -> GradingScaleOut:
        if data.is_default:
            await self._clear_default_scales(school_id)
        scale = GradingScale(
            school_id=school_id,
            name=data.name.strip(),
            is_default=data.is_default,
            is_active=data.is_active,
            must_pass_all_subjects=data.must_pass_all_subjects,
        )
        self.db.add(scale)
        await self.db.flush()
        await self._replace_rules(school_id, scale.id, data.rules)
        await self.db.commit()
        return await self._scale_out(scale.id)

    async def update_grading_scale(
        self, school_id: UUID, scale_id: UUID, data: GradingScaleUpdate
    ) -> GradingScaleOut:
        scale = await self.db.get(GradingScale, scale_id)
        if not scale or scale.school_id != school_id:
            raise NotFoundError("Grading scale not found")
        payload = data.model_dump(exclude_unset=True)
        rules = payload.pop("rules", None)
        if payload.get("is_default"):
            await self._clear_default_scales(school_id)
        for k, v in payload.items():
            setattr(scale, k, v.strip() if k == "name" and isinstance(v, str) else v)
        if rules is not None:
            await self._replace_rules(school_id, scale.id, [GradeRuleIn(**r) for r in rules])
        await self.db.commit()
        return await self._scale_out(scale.id)

    async def _clear_default_scales(self, school_id: UUID) -> None:
        rows = (
            await self.db.execute(select(GradingScale).where(GradingScale.school_id == school_id))
        ).scalars().all()
        for r in rows:
            r.is_default = False

    async def _replace_rules(self, school_id: UUID, scale_id: UUID, rules: list[GradeRuleIn]) -> None:
        existing = (
            await self.db.execute(select(GradeRule).where(GradeRule.grading_scale_id == scale_id))
        ).scalars().all()
        for e in existing:
            await self.db.delete(e)
        for rule in rules:
            self.db.add(
                GradeRule(
                    school_id=school_id,
                    grading_scale_id=scale_id,
                    grade=rule.grade,
                    min_percentage=rule.min_percentage,
                    max_percentage=rule.max_percentage,
                    description=rule.description,
                    sort_order=rule.sort_order,
                )
            )

    async def _scale_out(self, scale_id: UUID) -> GradingScaleOut:
        scale = (
            await self.db.execute(
                select(GradingScale)
                .where(GradingScale.id == scale_id)
                .options(selectinload(GradingScale.rules))
            )
        ).scalar_one()
        return GradingScaleOut(
            id=scale.id,
            school_id=scale.school_id,
            name=scale.name,
            is_default=scale.is_default,
            is_active=getattr(scale, "is_active", True),
            must_pass_all_subjects=scale.must_pass_all_subjects,
            rules=[GradeRuleOut.model_validate(r) for r in sorted(scale.rules, key=lambda x: x.sort_order)],
        )

    # ── exams CRUD ───────────────────────────────────────────

    async def _exam_class_ids(self, exam: Exam) -> set[UUID]:
        sections = (
            await self.db.execute(select(ExamSection.class_id).where(ExamSection.exam_id == exam.id))
        ).scalars().all()
        ids = set(sections)
        if exam.class_id:
            ids.add(exam.class_id)
        return ids

    async def _assert_teacher_sees_exam(self, school_id: UUID, user_id: UUID, exam: Exam) -> None:
        from src.modules.access.scope import teacher_class_ids

        classes = await teacher_class_ids(self.db, user_id, school_id)
        if not (await self._exam_class_ids(exam)).intersection(classes):
            raise NotFoundError("Exam not found")

    async def list_exams(
        self,
        school_id: UUID,
        *,
        academic_year_id: UUID | None = None,
        status: str | None = None,
        grade_id: UUID | None = None,
        user_id: UUID | None = None,
        role: str | None = None,
    ) -> list[ExamOut]:
        q = select(Exam).where(Exam.school_id == school_id).order_by(Exam.created_at.desc())
        if academic_year_id:
            q = q.where(Exam.academic_year_id == academic_year_id)
        if status:
            q = q.where(Exam.status == status)
        if grade_id:
            q = q.where(Exam.grade_id == grade_id)
        rows = list((await self.db.execute(q)).scalars().all())
        if role == UserRole.TEACHER.value and user_id is not None:
            from src.modules.access.scope import teacher_class_ids

            classes = await teacher_class_ids(self.db, user_id, school_id)
            visible = []
            for row in rows:
                if (await self._exam_class_ids(row)).intersection(classes):
                    visible.append(row)
            rows = visible
        return [await self._to_exam_out(r) for r in rows]

    async def get_exam(
        self,
        school_id: UUID,
        exam_id: UUID,
        *,
        user_id: UUID | None = None,
        role: str | None = None,
    ) -> ExamOut:
        exam = await self._get_exam(school_id, exam_id)
        if role == UserRole.TEACHER.value and user_id is not None:
            await self._assert_teacher_sees_exam(school_id, user_id, exam)
        return await self._to_exam_out(exam)

    async def create_exam(
        self, school_id: UUID, data: ExamCreate, *, created_by: UUID | None = None
    ) -> ExamOut:
        exam = Exam(
            school_id=school_id,
            academic_year_id=data.academic_year_id,
            exam_type_id=data.exam_type_id,
            grade_id=data.grade_id,
            grading_scale_id=data.grading_scale_id,
            title=data.title.strip(),
            description=data.description,
            starts_on=data.starts_on,
            ends_on=data.ends_on,
            status=data.status.value,
            must_pass_all_subjects=data.must_pass_all_subjects,
            max_score=100.0,
            created_by=created_by,
        )
        self.db.add(exam)
        await self.db.flush()
        for cid in data.class_ids:
            room = await self.db.get(ClassRoom, cid)
            if not room or room.school_id != school_id:
                raise NotFoundError("Class/section not found")
            self.db.add(ExamSection(school_id=school_id, exam_id=exam.id, class_id=cid))
        await self.db.commit()
        await self.db.refresh(exam)
        return await self._to_exam_out(exam)

    async def update_exam(self, school_id: UUID, exam_id: UUID, data: ExamUpdate) -> ExamOut:
        exam = await self._get_exam(school_id, exam_id)
        if exam.locked and data.status not in (None, ExamStatus.CLOSED):
            raise AppError("Exam is locked; unlock before editing", status_code=409)
        payload = data.model_dump(exclude_unset=True)
        class_ids = payload.pop("class_ids", None)
        if "status" in payload and payload["status"] is not None:
            payload["status"] = (
                payload["status"].value if hasattr(payload["status"], "value") else payload["status"]
            )
        for k, v in payload.items():
            setattr(exam, k, v.strip() if k == "title" and isinstance(v, str) else v)
        if class_ids is not None:
            existing = (
                await self.db.execute(select(ExamSection).where(ExamSection.exam_id == exam.id))
            ).scalars().all()
            for e in existing:
                await self.db.delete(e)
            for cid in class_ids:
                room = await self.db.get(ClassRoom, cid)
                if not room or room.school_id != school_id:
                    raise NotFoundError("Class/section not found")
                self.db.add(ExamSection(school_id=school_id, exam_id=exam.id, class_id=cid))
        await self.db.commit()
        await self.db.refresh(exam)
        return await self._to_exam_out(exam)

    async def delete_exam(self, school_id: UUID, exam_id: UUID) -> None:
        exam = await self._get_exam(school_id, exam_id)
        if exam.status == ExamStatus.PUBLISHED.value or exam.locked:
            raise ConflictError("Cannot delete published/locked exam")
        await self.db.delete(exam)
        await self.db.commit()

    # ── subjects & schedule ──────────────────────────────────

    async def add_subject(
        self, school_id: UUID, exam_id: UUID, data: ExamSubjectCreate
    ) -> ExamSubjectOut:
        exam = await self._get_exam(school_id, exam_id)
        if exam.locked:
            raise ConflictError("Exam is locked")
        sub = await self.db.get(Subject, data.subject_id)
        if not sub or sub.school_id != school_id:
            raise NotFoundError("Subject not found")
        existing = (
            await self.db.execute(
                select(ExamSubject).where(
                    ExamSubject.exam_id == exam_id, ExamSubject.subject_id == data.subject_id
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ConflictError("Subject already added to this exam")
        row = ExamSubject(
            school_id=school_id,
            exam_id=exam_id,
            subject_id=data.subject_id,
            grade_id=data.grade_id or exam.grade_id,
            max_marks=data.max_marks,
            pass_marks=data.pass_marks,
            duration_minutes=data.duration_minutes,
            instructions=data.instructions,
            exam_date=data.exam_date,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return ExamSubjectOut(
            id=row.id,
            exam_id=row.exam_id,
            subject_id=row.subject_id,
            subject_name=sub.name,
            grade_id=row.grade_id,
            max_marks=row.max_marks,
            pass_marks=row.pass_marks,
            duration_minutes=row.duration_minutes,
            instructions=row.instructions,
            exam_date=row.exam_date,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )

    async def update_subject(
        self, school_id: UUID, exam_id: UUID, subject_row_id: UUID, data: ExamSubjectUpdate
    ) -> ExamSubjectOut:
        exam = await self._get_exam(school_id, exam_id)
        if exam.locked:
            raise ConflictError("Exam is locked")
        row = await self.db.get(ExamSubject, subject_row_id)
        if not row or row.exam_id != exam_id or row.school_id != school_id:
            raise NotFoundError("Exam subject not found")
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(row, k, v)
        if row.pass_marks > row.max_marks:
            raise AppError("pass_marks cannot exceed max_marks", status_code=422)
        await self.db.commit()
        await self.db.refresh(row)
        return ExamSubjectOut(
            id=row.id,
            exam_id=row.exam_id,
            subject_id=row.subject_id,
            subject_name=await self._subject_name(row.subject_id),
            grade_id=row.grade_id,
            max_marks=row.max_marks,
            pass_marks=row.pass_marks,
            duration_minutes=row.duration_minutes,
            instructions=getattr(row, "instructions", None),
            exam_date=row.exam_date,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )

    async def delete_subject(self, school_id: UUID, exam_id: UUID, subject_row_id: UUID) -> None:
        exam = await self._get_exam(school_id, exam_id)
        if exam.locked:
            raise ConflictError("Exam is locked")
        row = await self.db.get(ExamSubject, subject_row_id)
        if not row or row.exam_id != exam_id or row.school_id != school_id:
            raise NotFoundError("Exam subject not found")
        await self.db.delete(row)
        await self.db.commit()

    async def list_schedule(
        self,
        school_id: UUID,
        exam_id: UUID,
        *,
        user_id: UUID | None = None,
        role: str | None = None,
    ) -> list[ExamScheduleOut]:
        exam = await self._get_exam(school_id, exam_id)
        if role == UserRole.TEACHER.value and user_id is not None:
            await self._assert_teacher_sees_exam(school_id, user_id, exam)
        out = await self._to_exam_out(exam)
        return out.schedules

    async def upsert_schedule(
        self, school_id: UUID, exam_id: UUID, items: list[ExamScheduleCreate]
    ) -> list[ExamScheduleOut]:
        exam = await self._get_exam(school_id, exam_id)
        if exam.locked:
            raise ConflictError("Exam is locked")
        self._validate_schedule_items(exam, items)
        existing = (
            await self.db.execute(select(ExamSchedule).where(ExamSchedule.exam_id == exam_id))
        ).scalars().all()
        for e in existing:
            await self.db.delete(e)
        for item in items:
            es = await self.db.get(ExamSubject, item.exam_subject_id)
            if not es or es.exam_id != exam_id:
                raise NotFoundError("Exam subject not found")
            self.db.add(
                ExamSchedule(
                    school_id=school_id,
                    exam_id=exam_id,
                    exam_subject_id=item.exam_subject_id,
                    class_id=item.class_id,
                    exam_date=item.exam_date,
                    starts_at=item.starts_at,
                    ends_at=item.ends_at,
                    room=item.room,
                )
            )
            if item.exam_date and not es.exam_date:
                es.exam_date = item.exam_date
                es.starts_at = item.starts_at
                es.ends_at = item.ends_at
        if exam.status == ExamStatus.DRAFT.value:
            exam.status = ExamStatus.SCHEDULED.value
            await self._notify_exam_published(exam)
        await self.db.commit()
        return await self.list_schedule(school_id, exam_id)

    def _validate_schedule_items(self, exam: Exam, items: list[ExamScheduleCreate]) -> None:
        # Date window + start < end + section time conflicts
        for item in items:
            if exam.starts_on and item.exam_date < exam.starts_on:
                raise AppError("exam_date is before exam start date", status_code=422)
            if exam.ends_on and item.exam_date > exam.ends_on:
                raise AppError("exam_date is after exam end date", status_code=422)
            if item.starts_at and item.ends_at and item.ends_at <= item.starts_at:
                raise AppError("start_time must be before end_time", status_code=422)

        # Section conflict: same class_id (or grade-wide) overlapping datetime
        by_section: dict[UUID | None, list[ExamScheduleCreate]] = {}
        for item in items:
            by_section.setdefault(item.class_id, []).append(item)
        for _sid, rows in by_section.items():
            for i, a in enumerate(rows):
                for b in rows[i + 1 :]:
                    if a.exam_date != b.exam_date:
                        continue
                    if a.exam_subject_id == b.exam_subject_id:
                        continue
                    if self._times_overlap(a.starts_at, a.ends_at, b.starts_at, b.ends_at):
                        raise AppError(
                            "Schedule conflict: two subjects overlap for the same section/date",
                            status_code=422,
                        )
            # Also check room conflicts within same date/time
            for i, a in enumerate(rows):
                if not a.room:
                    continue
                for b in rows[i + 1 :]:
                    if a.exam_date != b.exam_date or not b.room or a.room != b.room:
                        continue
                    if self._times_overlap(a.starts_at, a.ends_at, b.starts_at, b.ends_at):
                        raise AppError(
                            f"Room conflict: {a.room} already booked at that time",
                            status_code=422,
                        )

        # Cross-section room conflicts
        with_room = [i for i in items if i.room]
        for i, a in enumerate(with_room):
            for b in with_room[i + 1 :]:
                if a.exam_date != b.exam_date or a.room != b.room:
                    continue
                if self._times_overlap(a.starts_at, a.ends_at, b.starts_at, b.ends_at):
                    raise AppError(
                        f"Room conflict: {a.room} already booked at that time",
                        status_code=422,
                    )

    @staticmethod
    def _times_overlap(a_start, a_end, b_start, b_end) -> bool:
        # If either side missing times, treat same-date as conflict to be safe
        if not a_start or not a_end or not b_start or not b_end:
            return True
        return a_start < b_end and b_start < a_end

    async def open_for_marks(self, school_id: UUID, exam_id: UUID) -> ExamOut:
        """Publish exam for teacher marks entry (not results)."""
        exam = await self._get_exam(school_id, exam_id)
        subjects = (
            await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam_id))
        ).scalars().all()
        sections = (
            await self.db.execute(select(ExamSection).where(ExamSection.exam_id == exam_id))
        ).scalars().all()
        if not subjects:
            raise AppError("Add subjects before opening for marks", status_code=422)
        if not sections and not exam.class_id:
            raise AppError("Add sections before opening for marks", status_code=422)
        if exam.status in (ExamStatus.DRAFT.value, ExamStatus.SCHEDULED.value):
            exam.status = ExamStatus.MARKS_ENTRY.value
        await self.db.commit()
        return await self._to_exam_out(exam)

    async def list_teacher_exams(self, school_id: UUID, user_id: UUID) -> list[TeacherExamOut]:
        teacher = await self._teacher_for_user(school_id, user_id)
        assignments = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher.id,
                )
            )
        ).scalars().all()
        if not assignments:
            return []
        pairs = {(a.class_id, a.subject_id) for a in assignments}
        exams = (
            await self.db.execute(
                select(Exam)
                .where(
                    Exam.school_id == school_id,
                    Exam.status.in_(
                        [
                            ExamStatus.SCHEDULED.value,
                            ExamStatus.ONGOING.value,
                            ExamStatus.MARKS_ENTRY.value,
                            ExamStatus.UNDER_REVIEW.value,
                            ExamStatus.PUBLISHED.value,
                            ExamStatus.CLOSED.value,
                        ]
                    ),
                )
                .order_by(Exam.starts_on.desc().nullslast(), Exam.created_at.desc())
            )
        ).scalars().all()
        out: list[TeacherExamOut] = []
        for exam in exams:
            sections = (
                await self.db.execute(select(ExamSection).where(ExamSection.exam_id == exam.id))
            ).scalars().all()
            subjects = (
                await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam.id))
            ).scalars().all()
            section_ids = {s.class_id for s in sections} or (
                {exam.class_id} if exam.class_id else set()
            )
            for es in subjects:
                for cid in section_ids:
                    if (cid, es.subject_id) not in pairs:
                        continue
                    entry = (
                        await self.db.execute(
                            select(MarksEntry).where(
                                MarksEntry.exam_subject_id == es.id,
                                MarksEntry.class_id == cid,
                            )
                        )
                    ).scalar_one_or_none()
                    student_count = (
                        await self.db.execute(
                            select(func.count())
                            .select_from(Enrollment)
                            .where(
                                Enrollment.class_id == cid,
                                Enrollment.status == EnrollmentStatus.ACTIVE.value,
                            )
                        )
                    ).scalar_one()
                    entered = (
                        await self.db.execute(
                            select(func.count())
                            .select_from(StudentMark)
                            .where(
                                StudentMark.exam_subject_id == es.id,
                                StudentMark.class_id == cid,
                                (
                                    (StudentMark.obtained_marks.is_not(None))
                                    | (
                                        StudentMark.attendance.in_(
                                            [
                                                ExamAttendanceStatus.ABSENT.value,
                                                ExamAttendanceStatus.EXCUSED.value,
                                            ]
                                        )
                                    )
                                ),
                            )
                        )
                    ).scalar_one()
                    out.append(
                        TeacherExamOut(
                            exam_id=exam.id,
                            exam_title=exam.title,
                            exam_status=exam.status,
                            exam_subject_id=es.id,
                            subject_id=es.subject_id,
                            subject_name=await self._subject_name(es.subject_id),
                            class_id=cid,
                            class_name=await self._class_label(cid),
                            max_marks=es.max_marks,
                            pass_marks=es.pass_marks,
                            entry_status=entry.status if entry else MarkEntryStatus.DRAFT.value,
                            student_count=int(student_count),
                            marks_entered=int(entered),
                            starts_on=exam.starts_on,
                            ends_on=exam.ends_on,
                        )
                    )
        return out

    async def get_marks_sheet(
        self,
        school_id: UUID,
        exam_id: UUID,
        *,
        exam_subject_id: UUID,
        class_id: UUID,
        user_id: UUID,
        role: str,
    ) -> MarksSheetOut:
        exam = await self._get_exam(school_id, exam_id)
        es = await self.db.get(ExamSubject, exam_subject_id)
        if not es or es.exam_id != exam_id:
            raise NotFoundError("Exam subject not found")
        if role == UserRole.TEACHER.value:
            teacher = await self._teacher_for_user(school_id, user_id)
            await self._assert_teacher_assignment(
                school_id, teacher.id, class_id, es.subject_id, exam.academic_year_id
            )
        entry = (
            await self.db.execute(
                select(MarksEntry).where(
                    MarksEntry.exam_subject_id == exam_subject_id,
                    MarksEntry.class_id == class_id,
                )
            )
        ).scalar_one_or_none()
        enrollments = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.school_id == school_id,
                    Enrollment.class_id == class_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()
        marks = {
            m.student_id: m
            for m in (
                await self.db.execute(
                    select(StudentMark).where(
                        StudentMark.exam_subject_id == exam_subject_id,
                        StudentMark.class_id == class_id,
                    )
                )
            ).scalars().all()
        }
        rows: list[MarkRowOut] = []
        for enr in enrollments:
            m = marks.get(enr.student_id)
            rows.append(
                MarkRowOut(
                    id=m.id if m else None,
                    student_id=enr.student_id,
                    student_name=await self._student_name(enr.student_id),
                    roll_number=enr.roll_number,
                    obtained_marks=m.obtained_marks if m else None,
                    max_marks=es.max_marks,
                    attendance=m.attendance if m else ExamAttendanceStatus.PRESENT.value,
                    grade=m.grade if m else None,
                    remarks=m.remarks if m else None,
                    status=m.status if m else MarkEntryStatus.DRAFT.value,
                )
            )
        rows.sort(key=lambda r: (r.roll_number or "", r.student_name or ""))
        return MarksSheetOut(
            exam_id=exam.id,
            exam_title=exam.title,
            exam_subject_id=es.id,
            subject_id=es.subject_id,
            subject_name=await self._subject_name(es.subject_id),
            class_id=class_id,
            class_name=await self._class_label(class_id),
            max_marks=es.max_marks,
            pass_marks=es.pass_marks,
            entry_status=entry.status if entry else MarkEntryStatus.DRAFT.value,
            locked=exam.locked or (entry.status == MarkEntryStatus.LOCKED.value if entry else False),
            rows=rows,
        )

    async def save_marks_draft(
        self, school_id: UUID, exam_id: UUID, user_id: UUID, data: MarksSaveIn
    ) -> MarksSheetOut:
        exam = await self._get_exam(school_id, exam_id)
        if exam.locked:
            raise ConflictError("Exam results are locked")
        es = await self.db.get(ExamSubject, data.exam_subject_id)
        if not es or es.exam_id != exam_id:
            raise NotFoundError("Exam subject not found")
        teacher = await self._teacher_for_user(school_id, user_id)
        await self._assert_teacher_assignment(
            school_id, teacher.id, data.class_id, es.subject_id, exam.academic_year_id
        )
        entry = await self._get_or_create_entry(
            school_id, exam_id, data.exam_subject_id, data.class_id, teacher.id
        )
        if entry.status in (MarkEntryStatus.SUBMITTED.value, MarkEntryStatus.LOCKED.value):
            raise ConflictError("Marks already submitted; ask admin to unlock")
        if exam.status in (ExamStatus.DRAFT.value, ExamStatus.SCHEDULED.value, ExamStatus.ONGOING.value):
            exam.status = ExamStatus.MARKS_ENTRY.value
        scale = await self._load_scale(school_id, exam.grading_scale_id)
        rules = list(scale.rules) if scale else []
        now = _utcnow()
        for row in data.rows:
            if row.obtained_marks is not None and row.obtained_marks > es.max_marks:
                raise AppError(
                    f"Marks cannot exceed maximum ({es.max_marks})",
                    status_code=422,
                )
            existing = (
                await self.db.execute(
                    select(StudentMark).where(
                        StudentMark.exam_subject_id == data.exam_subject_id,
                        StudentMark.student_id == row.student_id,
                    )
                )
            ).scalar_one_or_none()
            pct = None
            grade = None
            if (
                row.attendance == ExamAttendanceStatus.PRESENT
                and row.obtained_marks is not None
                and es.max_marks > 0
            ):
                pct = (row.obtained_marks / es.max_marks) * 100
                grade = self._grade_for_percentage(rules, pct)
            if existing:
                existing.obtained_marks = row.obtained_marks
                existing.attendance = row.attendance.value
                existing.remarks = row.remarks
                existing.max_marks = es.max_marks
                existing.grade = grade
                existing.status = MarkEntryStatus.DRAFT.value
                existing.updated_by = user_id
                existing.class_id = data.class_id
            else:
                self.db.add(
                    StudentMark(
                        school_id=school_id,
                        exam_id=exam_id,
                        exam_subject_id=data.exam_subject_id,
                        student_id=row.student_id,
                        class_id=data.class_id,
                        obtained_marks=row.obtained_marks,
                        max_marks=es.max_marks,
                        attendance=row.attendance.value,
                        grade=grade,
                        remarks=row.remarks,
                        status=MarkEntryStatus.DRAFT.value,
                        entered_by=user_id,
                        entered_at=now,
                        updated_by=user_id,
                    )
                )
        entry.status = MarkEntryStatus.DRAFT.value
        await self.db.commit()
        return await self.get_marks_sheet(
            school_id,
            exam_id,
            exam_subject_id=data.exam_subject_id,
            class_id=data.class_id,
            user_id=user_id,
            role=UserRole.TEACHER.value,
        )

    async def submit_marks(
        self,
        school_id: UUID,
        exam_id: UUID,
        user_id: UUID,
        *,
        exam_subject_id: UUID,
        class_id: UUID,
    ) -> MarksSheetOut:
        exam = await self._get_exam(school_id, exam_id)
        if exam.locked:
            raise ConflictError("Exam results are locked")
        es = await self.db.get(ExamSubject, exam_subject_id)
        if not es or es.exam_id != exam_id:
            raise NotFoundError("Exam subject not found")
        teacher = await self._teacher_for_user(school_id, user_id)
        await self._assert_teacher_assignment(
            school_id, teacher.id, class_id, es.subject_id, exam.academic_year_id
        )
        entry = await self._get_or_create_entry(
            school_id, exam_id, exam_subject_id, class_id, teacher.id
        )
        if entry.status == MarkEntryStatus.LOCKED.value:
            raise ConflictError("Marks are locked")
        marks = (
            await self.db.execute(
                select(StudentMark).where(
                    StudentMark.exam_subject_id == exam_subject_id,
                    StudentMark.class_id == class_id,
                )
            )
        ).scalars().all()
        if not marks:
            raise AppError("Save draft marks before submitting", status_code=422)
        now = _utcnow()
        for m in marks:
            m.status = MarkEntryStatus.SUBMITTED.value
            m.updated_by = user_id
        entry.status = MarkEntryStatus.SUBMITTED.value
        entry.submitted_at = now
        exam.status = ExamStatus.UNDER_REVIEW.value
        await self.db.commit()
        for m in marks:
            await self._recompute_student_progress(
                school_id=school_id,
                student_id=m.student_id,
                academic_year_id=exam.academic_year_id,
            )
        return await self.get_marks_sheet(
            school_id,
            exam_id,
            exam_subject_id=exam_subject_id,
            class_id=class_id,
            user_id=user_id,
            role=UserRole.TEACHER.value,
        )

    async def _get_or_create_entry(
        self,
        school_id: UUID,
        exam_id: UUID,
        exam_subject_id: UUID,
        class_id: UUID,
        teacher_id: UUID,
    ) -> MarksEntry:
        entry = (
            await self.db.execute(
                select(MarksEntry).where(
                    MarksEntry.exam_subject_id == exam_subject_id,
                    MarksEntry.class_id == class_id,
                )
            )
        ).scalar_one_or_none()
        if entry:
            return entry
        entry = MarksEntry(
            school_id=school_id,
            exam_id=exam_id,
            exam_subject_id=exam_subject_id,
            class_id=class_id,
            teacher_id=teacher_id,
            status=MarkEntryStatus.DRAFT.value,
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def unlock_marks(
        self, school_id: UUID, exam_id: UUID, user_id: UUID, data: UnlockMarksIn
    ) -> MarksSheetOut:
        exam = await self._get_exam(school_id, exam_id)
        entry = (
            await self.db.execute(
                select(MarksEntry).where(
                    MarksEntry.exam_subject_id == data.exam_subject_id,
                    MarksEntry.class_id == data.class_id,
                )
            )
        ).scalar_one_or_none()
        if not entry:
            raise NotFoundError("Marks entry not found")
        marks = (
            await self.db.execute(
                select(StudentMark).where(
                    StudentMark.exam_subject_id == data.exam_subject_id,
                    StudentMark.class_id == data.class_id,
                )
            )
        ).scalars().all()
        for m in marks:
            self.db.add(
                ResultAuditLog(
                    school_id=school_id,
                    exam_id=exam_id,
                    student_id=m.student_id,
                    exam_subject_id=data.exam_subject_id,
                    old_marks=m.obtained_marks,
                    new_marks=m.obtained_marks,
                    changed_by=user_id,
                    reason=f"Unlock for edit: {data.reason}",
                )
            )
            m.status = MarkEntryStatus.DRAFT.value
        entry.status = MarkEntryStatus.DRAFT.value
        entry.submitted_at = None
        exam.locked = False
        if exam.status == ExamStatus.PUBLISHED.value:
            exam.status = ExamStatus.UNDER_REVIEW.value
            exam.published_at = None
            results = (
                await self.db.execute(select(Result).where(Result.exam_id == exam_id))
            ).scalars().all()
            for r in results:
                r.is_published = False
                r.published_at = None
        await self.db.commit()
        return await self.get_marks_sheet(
            school_id,
            exam_id,
            exam_subject_id=data.exam_subject_id,
            class_id=data.class_id,
            user_id=user_id,
            role=UserRole.SCHOOL_ADMIN.value,
        )

    async def correct_mark(
        self, school_id: UUID, exam_id: UUID, user_id: UUID, data: MarksCorrectionIn
    ) -> MarkRowOut:
        exam = await self._get_exam(school_id, exam_id)
        es = await self.db.get(ExamSubject, data.exam_subject_id)
        if not es or es.exam_id != exam_id:
            raise NotFoundError("Exam subject not found")
        mark = (
            await self.db.execute(
                select(StudentMark).where(
                    StudentMark.exam_subject_id == data.exam_subject_id,
                    StudentMark.student_id == data.student_id,
                )
            )
        ).scalar_one_or_none()
        if not mark:
            raise NotFoundError("Student mark not found")
        if data.obtained_marks is not None and data.obtained_marks > es.max_marks:
            raise AppError(f"Marks cannot exceed maximum ({es.max_marks})", status_code=422)
        old = mark.obtained_marks
        if data.obtained_marks is not None:
            mark.obtained_marks = data.obtained_marks
        if data.attendance is not None:
            mark.attendance = data.attendance.value
        scale = await self._load_scale(school_id, exam.grading_scale_id)
        rules = list(scale.rules) if scale else []
        if (
            mark.attendance == ExamAttendanceStatus.PRESENT.value
            and mark.obtained_marks is not None
            and es.max_marks > 0
        ):
            mark.grade = self._grade_for_percentage(
                rules, (mark.obtained_marks / es.max_marks) * 100
            )
        else:
            mark.grade = None
        mark.updated_by = user_id
        self.db.add(
            ResultAuditLog(
                school_id=school_id,
                exam_id=exam_id,
                student_id=data.student_id,
                exam_subject_id=data.exam_subject_id,
                old_marks=old,
                new_marks=mark.obtained_marks,
                changed_by=user_id,
                reason=data.reason,
            )
        )
        await self.db.commit()
        return MarkRowOut(
            id=mark.id,
            student_id=mark.student_id,
            student_name=await self._student_name(mark.student_id),
            roll_number=None,
            obtained_marks=mark.obtained_marks,
            max_marks=mark.max_marks,
            attendance=mark.attendance,
            grade=mark.grade,
            remarks=mark.remarks,
            status=mark.status,
        )

    # ── publish / calculate ──────────────────────────────────

    async def calculate_results(
        self, school_id: UUID, exam_id: UUID, *, allow_incomplete: bool = False
    ) -> CalculateResultsOut:
        exam = await self._get_exam(school_id, exam_id)
        sections, subjects, class_ids = await self._exam_scope(school_id, exam)
        await self._assert_marks_ready(exam_id, class_ids, subjects, allow_incomplete=allow_incomplete)

        scale = await self._load_scale(school_id, exam.grading_scale_id)
        rules = list(scale.rules) if scale else []
        must_pass_all = scale.must_pass_all_subjects if scale else exam.must_pass_all_subjects

        count = 0
        incomplete = 0
        passed = 0
        failed = 0
        for class_id in class_ids:
            enrollments = (
                await self.db.execute(
                    select(Enrollment).where(
                        Enrollment.school_id == school_id,
                        Enrollment.class_id == class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalars().all()
            for enr in enrollments:
                result = await self._calculate_and_upsert_result(
                    school_id=school_id,
                    exam=exam,
                    student_id=enr.student_id,
                    class_id=class_id,
                    subjects=subjects,
                    rules=rules,
                    must_pass_all=must_pass_all,
                    published_at=None,
                    publish=False,
                )
                count += 1
                if result.pass_fail == "PASS":
                    passed += 1
                elif result.pass_fail == "FAIL":
                    failed += 1
                elif result.pass_fail in ("INCOMPLETE", "ABSENT"):
                    incomplete += 1

        exam.status = ExamStatus.UNDER_REVIEW.value
        await self.db.commit()
        return CalculateResultsOut(
            exam_id=exam.id,
            status=exam.status,
            results_count=count,
            incomplete_count=incomplete,
            passed=passed,
            failed=failed,
        )

    async def publish(
        self,
        school_id: UUID,
        exam_id: UUID,
        *,
        user_id: UUID | None = None,
        allow_incomplete: bool = False,
    ) -> PublishOut:
        # Ensure calculated first
        calc = await self.calculate_results(
            school_id, exam_id, allow_incomplete=allow_incomplete
        )
        exam = await self._get_exam(school_id, exam_id)
        now = _utcnow()
        notified: set[UUID] = set()

        results = (
            await self.db.execute(select(Result).where(Result.exam_id == exam_id))
        ).scalars().all()
        for r in results:
            r.is_published = True
            r.published_at = now
            r.published_by = user_id
            await self._notify_result_published(exam, r.student_id, notified)

        marks = (
            await self.db.execute(select(StudentMark).where(StudentMark.exam_id == exam_id))
        ).scalars().all()
        for m in marks:
            m.status = MarkEntryStatus.LOCKED.value
        entries = (
            await self.db.execute(select(MarksEntry).where(MarksEntry.exam_id == exam_id))
        ).scalars().all()
        for e in entries:
            e.status = MarkEntryStatus.LOCKED.value

        exam.status = ExamStatus.PUBLISHED.value
        exam.published_at = now
        exam.published_by = user_id
        exam.locked = True
        await self.db.commit()
        for m in marks:
            await self._recompute_student_progress(
                school_id=school_id,
                student_id=m.student_id,
                academic_year_id=exam.academic_year_id,
            )
        return PublishOut(
            exam_id=exam.id,
            status=exam.status,
            results_count=calc.results_count,
            published_at=now,
            passed=calc.passed,
            failed=calc.failed,
        )

    async def _exam_scope(
        self, school_id: UUID, exam: Exam
    ) -> tuple[list[ExamSection], list[ExamSubject], list[UUID]]:
        sections = (
            await self.db.execute(select(ExamSection).where(ExamSection.exam_id == exam.id))
        ).scalars().all()
        subjects = (
            await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam.id))
        ).scalars().all()
        if not subjects:
            raise AppError("Add subjects before calculating results", status_code=422)
        class_ids = [s.class_id for s in sections]
        if not class_ids and exam.class_id:
            class_ids = [exam.class_id]
        if not class_ids:
            raise AppError("Add sections before calculating results", status_code=422)
        return list(sections), list(subjects), class_ids

    async def _assert_marks_ready(
        self,
        exam_id: UUID,
        class_ids: list[UUID],
        subjects: list[ExamSubject],
        *,
        allow_incomplete: bool,
    ) -> None:
        pending = (
            await self.db.execute(
                select(MarksEntry).where(
                    MarksEntry.exam_id == exam_id,
                    MarksEntry.status == MarkEntryStatus.DRAFT.value,
                )
            )
        ).scalars().all()
        if pending and not allow_incomplete:
            raise AppError("All marks sheets must be submitted before publish", status_code=422)

        expected = len(class_ids) * len(subjects)
        submitted = (
            await self.db.execute(
                select(func.count())
                .select_from(MarksEntry)
                .where(
                    MarksEntry.exam_id == exam_id,
                    MarksEntry.status.in_(
                        [MarkEntryStatus.SUBMITTED.value, MarkEntryStatus.LOCKED.value]
                    ),
                )
            )
        ).scalar_one()
        if int(submitted) < expected and not allow_incomplete:
            raise AppError(
                f"Incomplete marks: {submitted}/{expected} sheets submitted",
                status_code=422,
            )

    async def _calculate_and_upsert_result(
        self,
        *,
        school_id: UUID,
        exam: Exam,
        student_id: UUID,
        class_id: UUID,
        subjects: list[ExamSubject],
        rules: list[GradeRule],
        must_pass_all: bool,
        published_at: datetime | None,
        publish: bool = False,
    ) -> Result:
        total_obtained = 0.0
        total_max = 0.0
        subject_fails = 0
        present_count = 0
        absent_count = 0
        missing_count = 0
        scored_max = 0.0

        for es in subjects:
            mark = (
                await self.db.execute(
                    select(StudentMark).where(
                        StudentMark.exam_subject_id == es.id,
                        StudentMark.student_id == student_id,
                    )
                )
            ).scalar_one_or_none()
            total_max += es.max_marks
            if not mark:
                missing_count += 1
                subject_fails += 1
                continue
            if mark.attendance == ExamAttendanceStatus.ABSENT.value:
                absent_count += 1
                subject_fails += 1
                continue
            if mark.attendance == ExamAttendanceStatus.EXCUSED.value:
                # Excused: exclude from max for percentage
                total_max -= es.max_marks
                continue
            if mark.obtained_marks is None:
                missing_count += 1
                subject_fails += 1
                continue
            present_count += 1
            scored_max += es.max_marks
            total_obtained += mark.obtained_marks
            if mark.obtained_marks < es.pass_marks:
                subject_fails += 1
            pct = (mark.obtained_marks / es.max_marks) * 100 if es.max_marks else None
            mark.grade = self._grade_for_percentage(rules, pct)

        denom = scored_max if scored_max > 0 else total_max
        percentage = round((total_obtained / denom) * 100, 2) if denom else None
        grade_label = self._grade_for_percentage(rules, percentage)

        if present_count == 0 and absent_count == len(subjects):
            pass_fail = "ABSENT"
        elif missing_count > 0:
            pass_fail = "INCOMPLETE"
        elif must_pass_all:
            pass_fail = "PASS" if subject_fails == 0 and present_count > 0 else "FAIL"
        else:
            overall_pass = percentage is not None and percentage >= 35
            pass_fail = "PASS" if overall_pass else "FAIL"

        existing = (
            await self.db.execute(
                select(Result).where(Result.exam_id == exam.id, Result.student_id == student_id)
            )
        ).scalar_one_or_none()
        if existing:
            existing.score = total_obtained
            existing.max_score = denom or total_max
            existing.percentage = percentage
            existing.grade_label = grade_label
            existing.pass_fail = pass_fail
            existing.class_id = class_id
            if publish:
                existing.is_published = True
                existing.published_at = published_at
            else:
                existing.is_published = False
                existing.published_at = None
            return existing
        row = Result(
            school_id=school_id,
            exam_id=exam.id,
            student_id=student_id,
            class_id=class_id,
            score=total_obtained,
            max_score=denom or total_max,
            percentage=percentage,
            grade_label=grade_label,
            pass_fail=pass_fail,
            is_published=publish,
            published_at=published_at if publish else None,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    @staticmethod
    def compute_totals(
        subject_marks: list[tuple[float | None, float, str, float]],
        *,
        must_pass_all: bool,
    ) -> dict:
        """Pure helper for tests.

        Each tuple: (obtained, max_marks, attendance, pass_marks)
        """
        total_obtained = 0.0
        scored_max = 0.0
        subject_fails = 0
        present = 0
        absent = 0
        missing = 0
        for obtained, max_m, attendance, pass_m in subject_marks:
            if attendance == ExamAttendanceStatus.ABSENT.value:
                absent += 1
                subject_fails += 1
                continue
            if attendance == ExamAttendanceStatus.EXCUSED.value:
                continue
            if obtained is None:
                missing += 1
                subject_fails += 1
                continue
            present += 1
            total_obtained += obtained
            scored_max += max_m
            if obtained < pass_m:
                subject_fails += 1
        percentage = round((total_obtained / scored_max) * 100, 2) if scored_max else None
        if present == 0 and absent == len(subject_marks):
            status = "ABSENT"
        elif missing > 0:
            status = "INCOMPLETE"
        elif must_pass_all:
            status = "PASS" if subject_fails == 0 and present > 0 else "FAIL"
        else:
            status = "PASS" if percentage is not None and percentage >= 35 else "FAIL"
        return {
            "total_obtained": total_obtained,
            "total_max": scored_max,
            "percentage": percentage,
            "pass_fail": status,
        }
    async def _notify_exam_published(self, exam: Exam) -> None:
        sections = (
            await self.db.execute(select(ExamSection).where(ExamSection.exam_id == exam.id))
        ).scalars().all()
        class_ids = [s.class_id for s in sections]
        if not class_ids and exam.class_id:
            class_ids = [exam.class_id]
        if not class_ids:
            return
        notified: set[UUID] = set()
        body = f"{exam.title} schedule has been published."
        for class_id in class_ids:
            enrollments = (
                await self.db.execute(
                    select(Enrollment).where(
                        Enrollment.school_id == exam.school_id,
                        Enrollment.class_id == class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalars().all()
            for enr in enrollments:
                student = await self.db.get(Student, enr.student_id)
                if student and student.user_id not in notified:
                    notified.add(student.user_id)
                    await create_in_app_notification(
                        self.db,
                        school_id=exam.school_id,
                        user_id=student.user_id,
                        title="Exam published",
                        body=body,
                        category="EXAM_PUBLISHED",
                        link_path="/student/exams",
                        meta={"exam_id": str(exam.id)},
                        entity_type="exam",
                        entity_id=exam.id,
                        priority="HIGH",
                        event_key=f"EXAM_PUBLISHED:{exam.id}:{student.user_id}",
                    )
                links = (
                    await self.db.execute(
                        select(StudentParent).where(
                            StudentParent.school_id == exam.school_id,
                            StudentParent.student_id == enr.student_id,
                        )
                    )
                ).scalars().all()
                for link in links:
                    parent = await self.db.get(Parent, link.parent_id)
                    if not parent or parent.user_id in notified:
                        continue
                    notified.add(parent.user_id)
                    name = await self._student_name(enr.student_id) or "Your child"
                    await create_in_app_notification(
                        self.db,
                        school_id=exam.school_id,
                        user_id=parent.user_id,
                        title="Exam published",
                        body=f"{name}: {body}",
                        category="EXAM_PUBLISHED",
                        link_path="/parent/exams",
                        meta={"exam_id": str(exam.id), "student_id": str(enr.student_id)},
                        entity_type="exam",
                        entity_id=exam.id,
                        priority="HIGH",
                        event_key=f"EXAM_PUBLISHED:{exam.id}:{parent.user_id}",
                    )

    async def _recompute_student_progress(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        academic_year_id: UUID | None = None,
    ) -> None:
        """Best-effort progress refresh after marks submit/publish."""
        try:
            from src.modules.progress.service import ProgressService

            await ProgressService(self.db).recompute(
                school_id=school_id,
                student_id=student_id,
                academic_year_id=academic_year_id,
            )
        except Exception:
            return

    async def _notify_result_published(
        self, exam: Exam, student_id: UUID, notified: set[UUID]
    ) -> None:
        body = f"{exam.title} results have been published."
        student = await self.db.get(Student, student_id)
        if student and student.user_id not in notified:
            notified.add(student.user_id)
            await create_in_app_notification(
                self.db,
                school_id=exam.school_id,
                user_id=student.user_id,
                title="Results published",
                body=body,
                category="RESULT_PUBLISHED",
                link_path="/student/results",
                meta={"exam_id": str(exam.id)},
                entity_type="exam",
                entity_id=exam.id,
                priority="HIGH",
                event_key=f"RESULT_PUBLISHED:{exam.id}:{student.user_id}",
            )
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == exam.school_id,
                    StudentParent.student_id == student_id,
                )
            )
        ).scalars().all()
        for link in links:
            parent = await self.db.get(Parent, link.parent_id)
            if not parent or parent.user_id in notified:
                continue
            notified.add(parent.user_id)
            name = await self._student_name(student_id) or "Your child"
            await create_in_app_notification(
                self.db,
                school_id=exam.school_id,
                user_id=parent.user_id,
                title="Results published",
                body=f"{name}: {body}",
                category="RESULT_PUBLISHED",
                link_path="/parent/results",
                meta={"exam_id": str(exam.id), "student_id": str(student_id)},
                entity_type="exam",
                entity_id=exam.id,
                priority="HIGH",
                event_key=f"RESULT_PUBLISHED:{exam.id}:{parent.user_id}:{student_id}",
            )

    async def overview(self, school_id: UUID, exam_id: UUID) -> ExamOverviewOut:
        exam = await self._get_exam(school_id, exam_id)
        sections = (
            await self.db.execute(select(ExamSection).where(ExamSection.exam_id == exam_id))
        ).scalars().all()
        subjects = (
            await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam_id))
        ).scalars().all()
        subject_count = max(len(subjects), 1)
        section_outs: list[ExamOverviewSection] = []
        total_students = 0
        for sec in sections:
            student_count = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Enrollment)
                    .where(
                        Enrollment.class_id == sec.class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalar_one()
            submitted_entries = (
                await self.db.execute(
                    select(func.count())
                    .select_from(MarksEntry)
                    .where(
                        MarksEntry.exam_id == exam_id,
                        MarksEntry.class_id == sec.class_id,
                        MarksEntry.status.in_(
                            [MarkEntryStatus.SUBMITTED.value, MarkEntryStatus.LOCKED.value]
                        ),
                    )
                )
            ).scalar_one()
            section_outs.append(
                ExamOverviewSection(
                    class_id=sec.class_id,
                    class_name=await self._class_label(sec.class_id),
                    student_count=int(student_count),
                    marks_submitted=int(submitted_entries),
                    marks_expected=subject_count,
                )
            )
            total_students += int(student_count)

        results = (
            await self.db.execute(select(Result).where(Result.exam_id == exam_id))
        ).scalars().all()
        percentages = [r.percentage for r in results if r.percentage is not None]
        passes = [r for r in results if r.pass_fail == "PASS"]
        fails = [r for r in results if r.pass_fail == "FAIL"]
        marks_complete = sum(1 for s in section_outs if s.marks_submitted >= s.marks_expected)
        return ExamOverviewOut(
            exam_id=exam.id,
            title=exam.title,
            status=exam.status,
            locked=exam.locked,
            published=exam.status == ExamStatus.PUBLISHED.value,
            student_count=total_students,
            marks_complete=marks_complete,
            passed=len(passes),
            failed=len(fails),
            average=round(sum(percentages) / len(percentages), 2) if percentages else None,
            highest=max(percentages) if percentages else None,
            lowest=min(percentages) if percentages else None,
            pass_percent=(
                round(len(passes) / len(results) * 100, 1) if results else None
            ),
            sections=section_outs,
        )

    # ── published results ────────────────────────────────────

    async def result_for_student(
        self, school_id: UUID, exam_id: UUID, student_id: UUID, *, require_published: bool = True
    ) -> StudentResultOut:
        exam = await self._get_exam(school_id, exam_id)
        result = (
            await self.db.execute(
                select(Result).where(Result.exam_id == exam_id, Result.student_id == student_id)
            )
        ).scalar_one_or_none()
        if require_published:
            if not result or not result.is_published or exam.status != ExamStatus.PUBLISHED.value:
                raise NotFoundError("Result not published")
        elif not result:
            raise NotFoundError("Result not found")

        subjects = (
            await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam_id))
        ).scalars().all()
        subject_outs: list[SubjectResultOut] = []
        for es in subjects:
            mark = (
                await self.db.execute(
                    select(StudentMark).where(
                        StudentMark.exam_subject_id == es.id,
                        StudentMark.student_id == student_id,
                    )
                )
            ).scalar_one_or_none()
            passed = None
            if mark and mark.attendance == ExamAttendanceStatus.PRESENT.value and mark.obtained_marks is not None:
                passed = mark.obtained_marks >= es.pass_marks
            subject_outs.append(
                SubjectResultOut(
                    exam_subject_id=es.id,
                    subject_id=es.subject_id,
                    subject_name=await self._subject_name(es.subject_id),
                    obtained_marks=mark.obtained_marks if mark else None,
                    max_marks=es.max_marks,
                    pass_marks=es.pass_marks,
                    attendance=mark.attendance if mark else ExamAttendanceStatus.ABSENT.value,
                    grade=mark.grade if mark else None,
                    passed=passed,
                )
            )

        enr = (
            await self.db.execute(
                select(Enrollment)
                .where(
                    Enrollment.student_id == student_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        student_row = await self.db.get(Student, student_id)
        return StudentResultOut(
            exam_id=exam.id,
            exam_title=exam.title,
            student_id=student_id,
            student_name=await self._student_name(student_id),
            class_id=result.class_id or (enr.class_id if enr else None),
            class_name=await self._class_label(result.class_id or (enr.class_id if enr else None)),
            roll_number=enr.roll_number if enr else None,
            admission_no=student_row.admission_no if student_row else None,
            total_obtained=result.score,
            total_max=result.max_score,
            percentage=result.percentage,
            grade_label=result.grade_label,
            pass_fail=result.pass_fail,
            is_published=result.is_published,
            published_at=result.published_at,
            subjects=subject_outs,
        )

    async def my_results(self, school_id: UUID, user_id: UUID) -> list[StudentResultOut]:
        student = await self._student_for_user(school_id, user_id)
        results = (
            await self.db.execute(
                select(Result).where(
                    Result.school_id == school_id,
                    Result.student_id == student.id,
                    Result.is_published.is_(True),
                )
            )
        ).scalars().all()
        out: list[StudentResultOut] = []
        for r in results:
            exam = await self.db.get(Exam, r.exam_id)
            if not exam or exam.status != ExamStatus.PUBLISHED.value:
                continue
            out.append(await self.result_for_student(school_id, r.exam_id, student.id))
        return out

    async def list_children(self, school_id: UUID, user_id: UUID) -> list[ChildBriefOut]:
        parent = await self._parent_for_user(school_id, user_id)
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
            enr = (
                await self.db.execute(
                    select(Enrollment)
                    .where(
                        Enrollment.student_id == link.student_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            out.append(
                ChildBriefOut(
                    student_id=link.student_id,
                    full_name=await self._student_name(link.student_id) or "Student",
                    class_name=await self._class_label(enr.class_id) if enr else None,
                )
            )
        return out

    async def child_results(
        self, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> list[StudentResultOut]:
        parent = await self._parent_for_user(school_id, user_id)
        await self._assert_parent_child(school_id, parent.id, student_id)
        results = (
            await self.db.execute(
                select(Result).where(
                    Result.school_id == school_id,
                    Result.student_id == student_id,
                    Result.is_published.is_(True),
                )
            )
        ).scalars().all()
        out: list[StudentResultOut] = []
        for r in results:
            exam = await self.db.get(Exam, r.exam_id)
            if exam and exam.status == ExamStatus.PUBLISHED.value:
                out.append(await self.result_for_student(school_id, r.exam_id, student_id))
        return out

    async def exam_results_admin(self, school_id: UUID, exam_id: UUID) -> list[StudentResultOut]:
        await self._get_exam(school_id, exam_id)
        results = (
            await self.db.execute(select(Result).where(Result.exam_id == exam_id))
        ).scalars().all()
        return [
            await self.result_for_student(
                school_id, exam_id, r.student_id, require_published=False
            )
            for r in results
        ]

    async def audit_logs(self, school_id: UUID, exam_id: UUID) -> list[AuditLogOut]:
        await self._get_exam(school_id, exam_id)
        rows = (
            await self.db.execute(
                select(ResultAuditLog)
                .where(ResultAuditLog.school_id == school_id, ResultAuditLog.exam_id == exam_id)
                .order_by(ResultAuditLog.created_at.desc())
            )
        ).scalars().all()
        return [AuditLogOut.model_validate(r) for r in rows]

    async def report_card_payload(
        self, school_id: UUID, exam_id: UUID, student_id: UUID
    ) -> dict:
        result = await self.result_for_student(school_id, exam_id, student_id, require_published=True)
        school = await self.db.get(School, school_id)
        return {
            "school_name": school.name if school else "School",
            "exam_title": result.exam_title,
            "student_name": result.student_name,
            "class_name": result.class_name,
            "roll_number": result.roll_number,
            "subjects": [
                {
                    "name": s.subject_name,
                    "max": s.max_marks,
                    "marks": s.obtained_marks,
                    "grade": s.grade,
                    "attendance": s.attendance,
                }
                for s in result.subjects
            ],
            "total_obtained": result.total_obtained,
            "total_max": result.total_max,
            "percentage": result.percentage,
            "grade_label": result.grade_label,
            "pass_fail": result.pass_fail,
        }
