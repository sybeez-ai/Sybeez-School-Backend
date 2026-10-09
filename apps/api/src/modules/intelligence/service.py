"""Learning-intelligence: derive insights from real progress + submission data."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import (
    ClassRoom,
    Enrollment,
    Homework,
    HomeworkSubmission,
    Parent,
    Student,
    StudentMark,
    StudentParent,
    StudentProgress,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.intelligence.schemas import (
    CohortAtRiskStudent,
    CohortFocusTopic,
    CohortIntelligenceReport,
    CohortOverview,
    FocusArea,
    IntelligenceReport,
    Strength,
)
from src.modules.progress.schemas import SubjectProgressOut
from src.shared.enums import (
    EnrollmentStatus,
    HomeworkSubmissionStatus,
    MarkEntryStatus,
    PersonStatus,
    UserRole,
)

# Thresholds (configurable constants)
FOCUS_THRESHOLD = 50.0   # overall below this → focus area
STRENGTH_THRESHOLD = 75.0  # overall at or above this → strength


def _practice_prompt(subject_name: str | None, overall: float | None) -> str:
    name = subject_name or "this subject"
    score = f"{overall:.1f}%" if overall is not None else "an unknown score"
    return (
        f"You scored {score} in {name}. "
        f"Try reviewing key concepts, working through practice problems, "
        f"and revisiting any homework that was returned or graded below 60%."
    )


class IntelligenceService:
    def __init__(self, db: AsyncSession):
        self.db = db

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
            return  # allow if not currently enrolled
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
        s = await self.db.get(Student, student_id)
        if not s or s.school_id != school_id:
            raise NotFoundError("Student not found")
        return s

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

    # ── subject name helper ───────────────────────────────────────────────────

    async def _subject_name(self, subject_id: UUID) -> str | None:
        s = await self.db.get(Subject, subject_id)
        return s.name if s else None

    # ── public API ────────────────────────────────────────────────────────────

    async def get_my_intelligence(
        self, *, school_id: UUID, user_id: UUID
    ) -> IntelligenceReport:
        student = await self._student_for_user(school_id, user_id)
        return await self._build_report(school_id=school_id, student_id=student.id)

    async def get_student_intelligence(
        self, *, school_id: UUID, user_id: UUID, role: str, student_id: UUID
    ) -> IntelligenceReport:
        await self._assert_can_view(
            school_id=school_id, user_id=user_id, role=role, student_id=student_id
        )
        return await self._build_report(school_id=school_id, student_id=student_id)


    async def get_cohort_intelligence(
        self, *, school_id: UUID, user_id: UUID, role: str
    ) -> CohortIntelligenceReport:
        """Aggregate focus / at-risk signals for school admin or teacher classes."""
        student_ids = await self._cohort_student_ids(
            school_id=school_id, user_id=user_id, role=role
        )
        scope = "teacher" if role == UserRole.TEACHER.value else "school"

        if not student_ids:
            return CohortIntelligenceReport(
                scope=scope,
                overview=CohortOverview(
                    students_tracked=0,
                    avg_mastery=None,
                    at_risk_count=0,
                    focus_topics=0,
                ),
                top_focus_topics=[],
                at_risk_students=[],
            )

        rows = list(
            (
                await self.db.execute(
                    select(StudentProgress).where(
                        StudentProgress.school_id == school_id,
                        StudentProgress.student_id.in_(student_ids),
                        StudentProgress.metric == "overall",
                    )
                )
            )
            .scalars()
            .all()
        )

        by_student: dict[UUID, list[float]] = {sid: [] for sid in student_ids}
        subject_stats: dict[UUID, list[float]] = {}
        for row in rows:
            by_student.setdefault(row.student_id, []).append(row.value)
            subject_stats.setdefault(row.subject_id, []).append(row.value)

        student_overalls: dict[UUID, float] = {}
        for sid, vals in by_student.items():
            if vals:
                student_overalls[sid] = sum(vals) / len(vals)

        tracked = [v for v in student_overalls.values()]
        avg_mastery = round(sum(tracked) / len(tracked), 1) if tracked else None

        focus_topics: list[CohortFocusTopic] = []
        for sid, vals in subject_stats.items():
            avg = sum(vals) / len(vals)
            needing = sum(1 for v in vals if v < FOCUS_THRESHOLD)
            if needing > 0 or avg < STRENGTH_THRESHOLD:
                name = await self._subject_name(sid) or "Subject"
                focus_topics.append(
                    CohortFocusTopic(
                        subject_id=sid,
                        subject=name,
                        topic=name,
                        avg_mastery=round(avg, 1),
                        students_needing_focus=needing,
                    )
                )
        focus_topics.sort(key=lambda t: (t.avg_mastery, -t.students_needing_focus))
        focus_topics = focus_topics[:8]

        at_risk: list[CohortAtRiskStudent] = []
        names = await self._student_names(list(student_ids))
        class_labels = await self._student_class_labels(school_id, list(student_ids))

        for sid in student_ids:
            subject_vals = [r.value for r in rows if r.student_id == sid]
            focus_count = sum(1 for v in subject_vals if v < FOCUS_THRESHOLD)
            overall = student_overalls.get(sid)
            if overall is None and not subject_vals:
                continue
            overall_val = overall if overall is not None else (
                sum(subject_vals) / len(subject_vals) if subject_vals else 0.0
            )
            if overall_val >= FOCUS_THRESHOLD and focus_count < 2:
                continue
            weak = sorted(
                [(r.subject_id, r.value) for r in rows if r.student_id == sid],
                key=lambda x: x[1],
            )
            top_focus = None
            if weak:
                top_focus = await self._subject_name(weak[0][0])
            at_risk.append(
                CohortAtRiskStudent(
                    student_id=sid,
                    student_name=names.get(sid, "Student"),
                    class_label=class_labels.get(sid),
                    overall_mastery=round(overall_val, 1),
                    top_focus=top_focus,
                    focus_count=focus_count,
                )
            )
        at_risk.sort(key=lambda s: s.overall_mastery)
        at_risk = at_risk[:25]

        return CohortIntelligenceReport(
            scope=scope,
            overview=CohortOverview(
                students_tracked=len(tracked) or len(student_ids),
                avg_mastery=avg_mastery,
                at_risk_count=len(at_risk),
                focus_topics=len(focus_topics),
            ),
            top_focus_topics=focus_topics,
            at_risk_students=at_risk,
        )

    async def _cohort_student_ids(
        self, *, school_id: UUID, user_id: UUID, role: str
    ) -> list[UUID]:
        if role == UserRole.SCHOOL_ADMIN.value:
            rows = (
                await self.db.execute(
                    select(Student.id).where(
                        Student.school_id == school_id,
                        Student.status == PersonStatus.ACTIVE.value,
                    )
                )
            ).scalars().all()
            return list(rows)

        if role == UserRole.TEACHER.value:
            teacher = await self._teacher_for_user(school_id, user_id)
            class_ids = (
                await self.db.execute(
                    select(TeacherAssignment.class_id).where(
                        TeacherAssignment.school_id == school_id,
                        TeacherAssignment.teacher_id == teacher.id,
                    )
                )
            ).scalars().all()
            if not class_ids:
                return []
            rows = (
                await self.db.execute(
                    select(Enrollment.student_id).where(
                        Enrollment.school_id == school_id,
                        Enrollment.class_id.in_(list(class_ids)),
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalars().all()
            return list(set(rows))

        raise ForbiddenError("Not allowed")

    async def _student_names(self, student_ids: list[UUID]) -> dict[UUID, str]:
        if not student_ids:
            return {}
        rows = (
            await self.db.execute(
                select(Student.id, User.full_name)
                .join(User, User.id == Student.user_id)
                .where(Student.id.in_(student_ids))
            )
        ).all()
        return {r[0]: r[1] or "Student" for r in rows}

    async def _student_class_labels(
        self, school_id: UUID, student_ids: list[UUID]
    ) -> dict[UUID, str]:
        if not student_ids:
            return {}
        rows = (
            await self.db.execute(
                select(Enrollment.student_id, ClassRoom.name)
                .join(ClassRoom, ClassRoom.id == Enrollment.class_id)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id.in_(student_ids),
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).all()
        return {r[0]: r[1] for r in rows}


    # ── core report builder ───────────────────────────────────────────────────

    async def _build_report(
        self, *, school_id: UUID, student_id: UUID
    ) -> IntelligenceReport:
        # 1. Pull StudentProgress rows
        sp_rows = list(
            (
                await self.db.execute(
                    select(StudentProgress).where(
                        StudentProgress.school_id == school_id,
                        StudentProgress.student_id == student_id,
                    )
                )
            )
            .scalars()
            .all()
        )

        # Group by subject
        by_subject: dict[UUID, dict[str, float]] = {}
        year_id: UUID | None = None
        for row in sp_rows:
            year_id = row.academic_year_id
            by_subject.setdefault(row.subject_id, {})[row.metric] = row.value

        # Build subject breakdown
        subject_breakdown: list[SubjectProgressOut] = []
        for sid, metrics in by_subject.items():
            hw_avg = metrics.get("homework_avg")
            ex_avg = metrics.get("exam_avg")
            overall = metrics.get("overall")
            if overall is None:
                vals = [v for v in (hw_avg, ex_avg) if v is not None]
                overall = sum(vals) / len(vals) if vals else None
            subject_breakdown.append(
                SubjectProgressOut(
                    subject_id=sid,
                    subject_name=await self._subject_name(sid),
                    homework_avg=hw_avg,
                    exam_avg=ex_avg,
                    overall=overall,
                )
            )

        # 2. Focus areas and strengths
        focus_areas: list[FocusArea] = []
        strengths: list[Strength] = []
        practice_prompts: list[str] = []
        overalls: list[float] = []

        for sb in subject_breakdown:
            if sb.overall is not None:
                overalls.append(sb.overall)
            if sb.overall is not None and sb.overall < FOCUS_THRESHOLD:
                prompt = _practice_prompt(sb.subject_name, sb.overall)
                focus_areas.append(
                    FocusArea(
                        subject_id=sb.subject_id,
                        subject_name=sb.subject_name,
                        overall=sb.overall,
                        practice_prompt=prompt,
                    )
                )
                practice_prompts.append(prompt)
            elif sb.overall is not None and sb.overall >= STRENGTH_THRESHOLD:
                strengths.append(
                    Strength(
                        subject_id=sb.subject_id,
                        subject_name=sb.subject_name,
                        overall=sb.overall,
                    )
                )

        grand_overall = sum(overalls) / len(overalls) if overalls else None

        # 3. Recent homework rate
        recent_hw_rate = await self._recent_homework_rate(
            school_id=school_id, student_id=student_id
        )

        # 4. Recent exam average
        recent_exam_avg = await self._recent_exam_avg(
            school_id=school_id, student_id=student_id
        )

        return IntelligenceReport(
            student_id=student_id,
            academic_year_id=year_id,
            subject_breakdown=subject_breakdown,
            focus_areas=focus_areas,
            strengths=strengths,
            practice_prompts=practice_prompts,
            recent_homework_rate=recent_hw_rate,
            recent_exam_avg=recent_exam_avg,
            overall=grand_overall,
        )

    # ── metrics helpers ───────────────────────────────────────────────────────

    async def _recent_homework_rate(
        self, *, school_id: UUID, student_id: UUID
    ) -> float | None:
        """
        Fraction of published homework (from the student's enrolled class) that
        the student has submitted or had graded — i.e. not PENDING.
        Returns None if there is no homework at all.
        """
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
            return None

        # Count all published homework for their class
        total_result = await self.db.execute(
            select(func.count(Homework.id)).where(
                Homework.school_id == school_id,
                Homework.class_id == enr.class_id,
                Homework.status == "PUBLISHED",
            )
        )
        total = total_result.scalar() or 0
        if total == 0:
            return None

        # Count submissions where student has submitted/graded/late/returned
        submitted_result = await self.db.execute(
            select(func.count(HomeworkSubmission.id)).where(
                HomeworkSubmission.school_id == school_id,
                HomeworkSubmission.student_id == student_id,
                HomeworkSubmission.status.in_(
                    [
                        HomeworkSubmissionStatus.SUBMITTED.value,
                        HomeworkSubmissionStatus.LATE.value,
                        HomeworkSubmissionStatus.GRADED.value,
                        HomeworkSubmissionStatus.RETURNED.value,
                    ]
                ),
            )
        )
        submitted = submitted_result.scalar() or 0
        return round(min(submitted / total, 1.0), 4)

    async def _recent_exam_avg(
        self, *, school_id: UUID, student_id: UUID
    ) -> float | None:
        """
        Mean percentage across all SUBMITTED/LOCKED StudentMark entries for
        the student.  Returns None if no exam marks exist.
        """
        marks = list(
            (
                await self.db.execute(
                    select(StudentMark).where(
                        StudentMark.school_id == school_id,
                        StudentMark.student_id == student_id,
                        StudentMark.status.in_(
                            [
                                MarkEntryStatus.SUBMITTED.value,
                                MarkEntryStatus.LOCKED.value,
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        pcts = [
            (sm.obtained_marks / sm.max_marks * 100)
            for sm in marks
            if sm.obtained_marks is not None and sm.max_marks > 0
        ]
        if not pcts:
            return None
        return round(sum(pcts) / len(pcts), 2)
