"""Business logic for student progress: read, summary, and admin recompute."""

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import (
    AcademicYear,
    Enrollment,
    ExamSubject,
    Homework,
    HomeworkSubmission,
    Parent,
    Result,
    Student,
    StudentMark,
    StudentParent,
    StudentProgress,
    Subject,
    Teacher,
    TeacherAssignment,
)
from src.modules.progress.repository import ProgressRepository
from src.modules.progress.schemas import (
    PracticeAttemptCreate,
    PracticeAttemptOut,
    ProgressRowOut,
    ProgressSummaryOut,
    RecomputeResult,
    SubjectProgressOut,
)
from src.shared.enums import EnrollmentStatus, HomeworkSubmissionStatus, MarkEntryStatus, UserRole


def _as_json_text(value: list | dict | str) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value)


class ProgressService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ProgressRepository(db)

    # ── identity helpers ──────────────────────────────────────────────────────

    async def _student_for_user(self, school_id: UUID, user_id: UUID) -> Student:
        student = (
            await self.db.execute(
                select(Student).where(
                    Student.school_id == school_id, Student.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if not student:
            raise ForbiddenError("Student profile required")
        return student

    async def _parent_for_user(self, school_id: UUID, user_id: UUID) -> Parent:
        parent = (
            await self.db.execute(
                select(Parent).where(
                    Parent.school_id == school_id, Parent.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        return parent

    async def _teacher_for_user(self, school_id: UUID, user_id: UUID) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(
                    Teacher.school_id == school_id, Teacher.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if not teacher:
            raise ForbiddenError("Teacher profile required")
        return teacher

    async def _assert_parent_of(
        self, *, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> None:
        parent = await self._parent_for_user(school_id, user_id)
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

    async def _assert_teacher_can_view(
        self, *, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> None:
        teacher = await self._teacher_for_user(school_id, user_id)
        enr = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id == student_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        if enr is None:
            return  # no active enrollment — allow teacher access
        assignments = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == enr.class_id,
                )
            )
        ).scalars().all()
        if not assignments:
            raise ForbiddenError("Student is not in one of your classes")

    async def _get_student(self, school_id: UUID, student_id: UUID) -> Student:
        student = await self.db.get(Student, student_id)
        if not student or student.school_id != school_id:
            raise NotFoundError("Student not found")
        return student

    async def _subject_name(self, subject_id: UUID) -> str | None:
        subject = await self.db.get(Subject, subject_id)
        return subject.name if subject else None

    # ── ACL gate ──────────────────────────────────────────────────────────────

    async def _assert_can_view(
        self, *, school_id: UUID, user_id: UUID, role: str, student_id: UUID
    ) -> None:
        if role == UserRole.SCHOOL_ADMIN.value:
            await self._get_student(school_id, student_id)
        elif role == UserRole.TEACHER.value:
            await self._assert_teacher_can_view(
                school_id=school_id, user_id=user_id, student_id=student_id
            )
        elif role == UserRole.PARENT.value:
            await self._assert_parent_of(
                school_id=school_id, user_id=user_id, student_id=student_id
            )
        else:
            raise ForbiddenError("Not allowed")

    # ── public read endpoints ─────────────────────────────────────────────────

    async def get_my_progress(
        self, *, school_id: UUID, user_id: UUID
    ) -> list[ProgressRowOut]:
        student = await self._student_for_user(school_id, user_id)
        rows = await self.repo.list_for_student(
            school_id=school_id, student_id=student.id
        )
        return [ProgressRowOut.model_validate(r) for r in rows]

    async def get_student_progress(
        self, *, school_id: UUID, user_id: UUID, role: str, student_id: UUID
    ) -> list[ProgressRowOut]:
        await self._assert_can_view(
            school_id=school_id, user_id=user_id, role=role, student_id=student_id
        )
        rows = await self.repo.list_for_student(
            school_id=school_id, student_id=student_id
        )
        return [ProgressRowOut.model_validate(r) for r in rows]

    async def get_my_summary(
        self, *, school_id: UUID, user_id: UUID
    ) -> ProgressSummaryOut:
        student = await self._student_for_user(school_id, user_id)
        return await self._build_summary(school_id=school_id, student_id=student.id)

    async def get_student_summary(
        self, *, school_id: UUID, user_id: UUID, role: str, student_id: UUID
    ) -> ProgressSummaryOut:
        await self._assert_can_view(
            school_id=school_id, user_id=user_id, role=role, student_id=student_id
        )
        return await self._build_summary(school_id=school_id, student_id=student_id)

    # ── summary builder ───────────────────────────────────────────────────────

    async def _build_summary(
        self, *, school_id: UUID, student_id: UUID
    ) -> ProgressSummaryOut:
        rows = await self.repo.list_for_student(
            school_id=school_id, student_id=student_id
        )
        # Group by subject_id
        by_subject: dict[UUID, dict[str, float]] = {}
        year_id: UUID | None = None
        for row in rows:
            year_id = row.academic_year_id
            by_subject.setdefault(row.subject_id, {})[row.metric] = row.value

        subjects_out: list[SubjectProgressOut] = []
        overalls: list[float] = []
        for sid, metrics in by_subject.items():
            hw_avg = metrics.get("homework_avg")
            ex_avg = metrics.get("exam_avg")
            overall = metrics.get("overall")
            if overall is None:
                vals = [v for v in (hw_avg, ex_avg) if v is not None]
                overall = sum(vals) / len(vals) if vals else None
            if overall is not None:
                overalls.append(overall)
            subjects_out.append(
                SubjectProgressOut(
                    subject_id=sid,
                    subject_name=await self._subject_name(sid),
                    homework_avg=hw_avg,
                    exam_avg=ex_avg,
                    overall=overall,
                )
            )

        grand = sum(overalls) / len(overalls) if overalls else None
        return ProgressSummaryOut(
            student_id=student_id,
            academic_year_id=year_id,
            subjects=subjects_out,
            overall=grand,
        )

    # ── recompute ─────────────────────────────────────────────────────────────

    async def recompute(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        academic_year_id: UUID | None = None,
    ) -> RecomputeResult:
        """
        Recompute StudentProgress rows for a student from graded homework and
        published/locked exam marks.  Admin-only.

        Metrics written per subject:
          - homework_avg  : weighted % across all graded homework in the class
          - exam_avg      : mean % across LOCKED/SUBMITTED StudentMark entries
          - overall       : mean of whichever metrics are available
        """
        await self._get_student(school_id, student_id)

        # Resolve academic year
        year_id = await self._resolve_year(school_id, academic_year_id)

        # Active enrollment → class
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

        if enr is None:
            return RecomputeResult(
                student_id=student_id,
                academic_year_id=year_id,
                subjects_processed=0,
                summary=ProgressSummaryOut(
                    student_id=student_id,
                    academic_year_id=year_id,
                    subjects=[],
                    overall=None,
                ),
            )

        class_id = enr.class_id

        # ── homework averages per subject ─────────────────────────────────────
        hw_stmt = select(Homework).where(
            Homework.school_id == school_id,
            Homework.class_id == class_id,
        )
        if year_id is not None:
            hw_stmt = hw_stmt.where(Homework.academic_year_id == year_id)
        homeworks = list((await self.db.execute(hw_stmt)).scalars().all())

        # subject_id → [(obtained, max)]
        hw_pairs: dict[UUID, list[tuple[float, float]]] = {}
        for hw in homeworks:
            if not hw.max_marks or hw.max_marks <= 0:
                continue
            sub = (
                await self.db.execute(
                    select(HomeworkSubmission).where(
                        HomeworkSubmission.homework_id == hw.id,
                        HomeworkSubmission.student_id == student_id,
                        HomeworkSubmission.status
                        == HomeworkSubmissionStatus.GRADED.value,
                    )
                )
            ).scalar_one_or_none()
            if sub is not None and sub.marks is not None:
                hw_pairs.setdefault(hw.subject_id, []).append(
                    (sub.marks, hw.max_marks)
                )

        hw_avg_by_subject: dict[UUID, float] = {}
        for sid, pairs in hw_pairs.items():
            total_obtained = sum(p[0] for p in pairs)
            total_max = sum(p[1] for p in pairs)
            hw_avg_by_subject[sid] = (total_obtained / total_max * 100) if total_max > 0 else 0.0

        # ── exam averages per subject via StudentMark ─────────────────────────
        sm_stmt = select(StudentMark).where(
            StudentMark.school_id == school_id,
            StudentMark.student_id == student_id,
            StudentMark.status.in_(
                [MarkEntryStatus.SUBMITTED.value, MarkEntryStatus.LOCKED.value]
            ),
        )
        student_marks = list((await self.db.execute(sm_stmt)).scalars().all())

        exam_pcts: dict[UUID, list[float]] = {}
        for sm in student_marks:
            if sm.obtained_marks is None or sm.max_marks <= 0:
                continue
            es = await self.db.get(ExamSubject, sm.exam_subject_id)
            if es is None:
                continue
            pct = (sm.obtained_marks / sm.max_marks) * 100
            exam_pcts.setdefault(es.subject_id, []).append(pct)

        exam_avg_by_subject: dict[UUID, float] = {
            sid: sum(pcts) / len(pcts) for sid, pcts in exam_pcts.items()
        }

        # ── upsert rows ───────────────────────────────────────────────────────
        all_subjects: set[UUID] = set(hw_avg_by_subject) | set(exam_avg_by_subject)
        for sid in all_subjects:
            hw_avg = hw_avg_by_subject.get(sid)
            ex_avg = exam_avg_by_subject.get(sid)
            if hw_avg is not None:
                await self.repo.upsert(
                    school_id=school_id,
                    student_id=student_id,
                    subject_id=sid,
                    academic_year_id=year_id,
                    metric="homework_avg",
                    value=round(hw_avg, 2),
                )
            if ex_avg is not None:
                await self.repo.upsert(
                    school_id=school_id,
                    student_id=student_id,
                    subject_id=sid,
                    academic_year_id=year_id,
                    metric="exam_avg",
                    value=round(ex_avg, 2),
                )
            vals = [v for v in (hw_avg, ex_avg) if v is not None]
            if vals:
                await self.repo.upsert(
                    school_id=school_id,
                    student_id=student_id,
                    subject_id=sid,
                    academic_year_id=year_id,
                    metric="overall",
                    value=round(sum(vals) / len(vals), 2),
                )

        summary = await self._build_summary(school_id=school_id, student_id=student_id)
        return RecomputeResult(
            student_id=student_id,
            academic_year_id=year_id,
            subjects_processed=len(all_subjects),
            summary=summary,
        )

    async def _resolve_year(
        self, school_id: UUID, academic_year_id: UUID | None
    ) -> UUID | None:
        if academic_year_id is not None:
            yr = await self.db.get(AcademicYear, academic_year_id)
            if not yr or yr.school_id != school_id:
                raise NotFoundError("Academic year not found")
            return yr.id
        # Try to find most recent academic year for this school
        yr = (
            await self.db.execute(
                select(AcademicYear)
                .where(AcademicYear.school_id == school_id)
                .order_by(AcademicYear.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return yr.id if yr else None

    # ── Practice attempts ─────────────────────────────────────────────────────

    async def create_practice_attempt(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        data: PracticeAttemptCreate,
    ) -> PracticeAttemptOut:
        # Ensure caller is a student in this school
        await self._student_for_user(school_id, user_id)
        row = await self.repo.create_practice_attempt(
            school_id=school_id,
            student_user_id=user_id,
            subject_id=data.subject_id,
            topic=data.topic,
            difficulty=data.difficulty or "adaptive",
            questions_json=_as_json_text(data.questions_json),
            answers_json=_as_json_text(data.answers_json),
            score_pct=data.score_pct,
        )
        await self.db.commit()
        await self.db.refresh(row)
        return PracticeAttemptOut(
            id=row.id,
            student_user_id=row.student_user_id,
            topic=row.topic,
            difficulty=row.difficulty,
            score_pct=float(row.score_pct) if row.score_pct is not None else None,
            created_at=row.created_at,
        )

    async def list_practice_attempts(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        limit: int = 10,
    ) -> list[PracticeAttemptOut]:
        await self._student_for_user(school_id, user_id)
        rows = await self.repo.list_practice_attempts(
            school_id=school_id,
            student_user_id=user_id,
            limit=min(max(limit, 1), 50),
        )
        return [
            PracticeAttemptOut(
                id=r.id,
                student_user_id=r.student_user_id,
                topic=r.topic,
                difficulty=r.difficulty,
                score_pct=float(r.score_pct) if r.score_pct is not None else None,
                created_at=r.created_at,
            )
            for r in rows
        ]
