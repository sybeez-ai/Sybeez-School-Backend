"""Analytics aggregations over existing Phase 1–10 modules (no data duplication)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import (
    AnalyticsSetting,
    Announcement,
    Attendance,
    ConversationMessage,
    Enrollment,
    Exam,
    FeeInvoice,
    Grade,
    Homework,
    HomeworkSubmission,
    Notification,
    Parent,
    Payment,
    Result,
    Student,
    StudentMark,
    StudentParent,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.db.models.class_ import ClassRoom
from src.db.models.exam import ExamSubject
from src.db.models.fee import StudentFeeAccount
from src.modules.analytics.schemas import (
    AcademicOut,
    AnalyticsFilters,
    AnalyticsSettingsOut,
    AnalyticsSettingsUpdate,
    AttendanceAnalyticsOut,
    AttentionItem,
    ClassAnalyticsOut,
    CommunicationAnalyticsOut,
    ExamAnalyticsOut,
    FeesAnalyticsOut,
    HomeworkAnalyticsOut,
    MetricPoint,
    OverviewOut,
    StudentAnalyticsOut,
    StudentsAnalyticsOut,
    TeacherAnalyticsOut,
)
from src.shared.enums import (
    AttendanceStatus,
    EnrollmentStatus,
    FeeInvoiceStatus,
    HomeworkStatus,
    HomeworkSubmissionStatus,
    PaymentStatus,
    UserRole,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pct(part: float | int, whole: float | int) -> float:
    if not whole:
        return 0.0
    return round(float(part) / float(whole) * 100, 1)


class AnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_settings(self, school_id: UUID) -> AnalyticsSetting:
        row = (
            await self.db.execute(
                select(AnalyticsSetting).where(AnalyticsSetting.school_id == school_id)
            )
        ).scalar_one_or_none()
        if row:
            return row
        row = AnalyticsSetting(id=uuid4(), school_id=school_id)
        self.db.add(row)
        await self.db.flush()
        return row

    async def update_settings(
        self, school_id: UUID, data: AnalyticsSettingsUpdate
    ) -> AnalyticsSettingsOut:
        row = await self.get_settings(school_id)
        if data.attendance_threshold is not None:
            row.attendance_threshold = data.attendance_threshold
        if data.missing_homework_threshold is not None:
            row.missing_homework_threshold = data.missing_homework_threshold
        if data.low_exam_percentage_threshold is not None:
            row.low_exam_percentage_threshold = data.low_exam_percentage_threshold
        await self.db.commit()
        await self.db.refresh(row)
        return AnalyticsSettingsOut.model_validate(row)

    def _enrollment_q(
        self,
        school_id: UUID,
        f: AnalyticsFilters,
        *,
        statuses: list[str] | None = None,
    ):
        q = select(Enrollment).where(Enrollment.school_id == school_id)
        if f.academic_year_id:
            q = q.where(Enrollment.academic_year_id == f.academic_year_id)
        if f.grade_id:
            q = q.where(Enrollment.grade_id == f.grade_id)
        if f.section_id:
            q = q.where(Enrollment.class_id == f.section_id)
        if statuses:
            q = q.where(Enrollment.status.in_(statuses))
        return q

    async def _student_name(self, student_id: UUID) -> str:
        st = await self.db.get(Student, student_id)
        if not st:
            return "Student"
        u = await self.db.get(User, st.user_id)
        return u.name if u else "Student"

    async def _class_name(self, class_id: UUID | None) -> str | None:
        if not class_id:
            return None
        c = await self.db.get(ClassRoom, class_id)
        if not c:
            return None
        return f"{c.name}{('-' + c.section) if getattr(c, 'section', None) else ''}"

    async def _linked_children(self, school_id: UUID, user_id: UUID) -> list[UUID]:
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not parent:
            return []
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent.id,
                )
            )
        ).scalars().all()
        return [l.student_id for l in links]

    async def _teacher_class_ids(self, school_id: UUID, user_id: UUID) -> list[UUID]:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            return []
        rows = (
            await self.db.execute(
                select(TeacherAssignment.class_id).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher.id,
                )
            )
        ).scalars().all()
        return list({r for r in rows})

    async def assert_student_access(
        self, school_id: UUID, user_id: UUID, role: str, student_id: UUID
    ) -> None:
        if role == UserRole.SCHOOL_ADMIN.value:
            return
        if role == UserRole.PARENT.value:
            if student_id not in await self._linked_children(school_id, user_id):
                raise ForbiddenError("Not linked to this student")
            return
        if role == UserRole.STUDENT.value:
            st = (
                await self.db.execute(
                    select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
                )
            ).scalar_one_or_none()
            if not st or st.id != student_id:
                raise ForbiddenError("Students can only view their own analytics")
            return
        if role == UserRole.TEACHER.value:
            class_ids = await self._teacher_class_ids(school_id, user_id)
            enr = (
                await self.db.execute(
                    select(Enrollment).where(
                        Enrollment.school_id == school_id,
                        Enrollment.student_id == student_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                        Enrollment.class_id.in_(class_ids or [uuid4()]),
                    )
                )
            ).scalar_one_or_none()
            if not enr:
                raise ForbiddenError("Student not in your assigned classes")
            return
        raise ForbiddenError("Not allowed")

    # ── overview ──────────────────────────────────────────────

    async def overview(self, school_id: UUID, f: AnalyticsFilters) -> OverviewOut:
        students = (
            await self.db.execute(
                select(func.count())
                .select_from(Enrollment)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    *(
                        [Enrollment.academic_year_id == f.academic_year_id]
                        if f.academic_year_id
                        else []
                    ),
                    *([Enrollment.grade_id == f.grade_id] if f.grade_id else []),
                    *([Enrollment.class_id == f.section_id] if f.section_id else []),
                )
            )
        ).scalar_one()
        teachers = (
            await self.db.execute(
                select(func.count()).select_from(Teacher).where(Teacher.school_id == school_id)
            )
        ).scalar_one()
        att = await self.attendance(school_id, f)
        hw = await self.homework(school_id, f)
        exams = await self.exams(school_id, f)
        fees = await self.fees(school_id, f)
        attention = await self.attention_items(school_id, f)
        return OverviewOut(
            students=int(students or 0),
            teachers=int(teachers or 0),
            attendance_rate=att.attendance_rate,
            homework_completion=hw.submission_rate,
            exam_average=exams.average or 0.0,
            fees_collection_rate=fees.collection_rate,
            attendance_trend=att.trend,
            exam_trend=exams.exam_trend,
            fee_trend=fees.trend,
            homework_trend=hw.by_class[:8],
            attention=attention[:12],
            currency=fees.currency,
            updated_at=_utcnow(),
        )

    async def academic(self, school_id: UUID, f: AnalyticsFilters) -> AcademicOut:
        active_q = self._enrollment_q(
            school_id, f, statuses=[EnrollmentStatus.ACTIVE.value]
        )
        active = (
            await self.db.execute(select(func.count()).select_from(active_q.subquery()))
        ).scalar_one()
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(self._enrollment_q(school_id, f).subquery())
            )
        ).scalar_one()
        teachers = (
            await self.db.execute(
                select(func.count()).select_from(Teacher).where(Teacher.school_id == school_id)
            )
        ).scalar_one()
        subjects = (
            await self.db.execute(
                select(func.count()).select_from(Subject).where(Subject.school_id == school_id)
            )
        ).scalar_one()

        grade_rows = (
            await self.db.execute(
                select(Grade.name, func.count(Enrollment.id))
                .join(Enrollment, Enrollment.grade_id == Grade.id)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    *(
                        [Enrollment.academic_year_id == f.academic_year_id]
                        if f.academic_year_id
                        else []
                    ),
                )
                .group_by(Grade.name)
                .order_by(Grade.name)
            )
        ).all()
        section_rows = (
            await self.db.execute(
                select(ClassRoom.name, ClassRoom.section, func.count(Enrollment.id))
                .join(Enrollment, Enrollment.class_id == ClassRoom.id)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    *(
                        [Enrollment.academic_year_id == f.academic_year_id]
                        if f.academic_year_id
                        else []
                    ),
                    *([Enrollment.grade_id == f.grade_id] if f.grade_id else []),
                )
                .group_by(ClassRoom.name, ClassRoom.section)
                .order_by(ClassRoom.name, ClassRoom.section)
            )
        ).all()
        return AcademicOut(
            total_students=int(total or 0),
            active_students=int(active or 0),
            teachers=int(teachers or 0),
            subjects=int(subjects or 0),
            by_grade=[MetricPoint(label=n or "—", value=float(c)) for n, c in grade_rows],
            by_section=[
                MetricPoint(
                    label=f"{n}{('-' + s) if s else ''}",
                    value=float(c),
                )
                for n, s, c in section_rows
            ],
        )

    async def students(self, school_id: UUID, f: AnalyticsFilters) -> StudentsAnalyticsOut:
        base = select(Enrollment.status, func.count()).where(Enrollment.school_id == school_id)
        if f.academic_year_id:
            base = base.where(Enrollment.academic_year_id == f.academic_year_id)
        if f.grade_id:
            base = base.where(Enrollment.grade_id == f.grade_id)
        if f.section_id:
            base = base.where(Enrollment.class_id == f.section_id)
        rows = (await self.db.execute(base.group_by(Enrollment.status))).all()
        counts = {s: int(c) for s, c in rows}
        active = counts.get(EnrollmentStatus.ACTIVE.value, 0)
        transferred = counts.get(EnrollmentStatus.TRANSFERRED.value, 0)
        withdrawn = counts.get(EnrollmentStatus.WITHDRAWN.value, 0)
        # Some DBs use LEFT / INACTIVE
        inactive = sum(v for k, v in counts.items() if k != EnrollmentStatus.ACTIVE.value)
        academic = await self.academic(school_id, f)
        return StudentsAnalyticsOut(
            total=sum(counts.values()),
            active=active,
            inactive=inactive,
            transferred=transferred,
            new_admissions=active,  # approximation without dedicated admissions table
            withdrawals=withdrawn or (inactive - transferred if inactive >= transferred else 0),
            by_grade=academic.by_grade,
        )

    async def attendance(self, school_id: UUID, f: AnalyticsFilters) -> AttendanceAnalyticsOut:
        q = select(Attendance.status, func.count()).where(Attendance.school_id == school_id)
        if f.from_date:
            q = q.where(Attendance.attended_on >= f.from_date)
        if f.to_date:
            q = q.where(Attendance.attended_on <= f.to_date)
        if f.section_id:
            q = q.where(Attendance.class_id == f.section_id)
        elif f.grade_id or f.academic_year_id:
            # Restrict via enrollments for historical year correctness
            enr = select(Enrollment.student_id, Enrollment.class_id).where(
                Enrollment.school_id == school_id
            )
            if f.academic_year_id:
                enr = enr.where(Enrollment.academic_year_id == f.academic_year_id)
            if f.grade_id:
                enr = enr.where(Enrollment.grade_id == f.grade_id)
            if f.section_id:
                enr = enr.where(Enrollment.class_id == f.section_id)
            sub = enr.subquery()
            q = q.join(
                sub,
                and_(Attendance.student_id == sub.c.student_id, Attendance.class_id == sub.c.class_id),
            )
        rows = (await self.db.execute(q.group_by(Attendance.status))).all()
        counts = {s: int(c) for s, c in rows}
        present = counts.get(AttendanceStatus.PRESENT.value, 0)
        absent = counts.get(AttendanceStatus.ABSENT.value, 0)
        late = counts.get(AttendanceStatus.LATE.value, 0)
        excused = counts.get(AttendanceStatus.EXCUSED.value, 0)
        half = counts.get(AttendanceStatus.HALF_DAY.value, 0)
        total = sum(counts.values())
        # Rate: present+late+half as attending
        attending = present + late + half
        rate = _pct(attending, total)

        # Monthly trend
        month_expr = func.date_trunc("month", Attendance.attended_on)
        tq = select(
            month_expr.label("m"),
            func.count().label("total"),
            func.sum(
                case(
                    (
                        Attendance.status.in_(
                            [
                                AttendanceStatus.PRESENT.value,
                                AttendanceStatus.LATE.value,
                                AttendanceStatus.HALF_DAY.value,
                            ]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("att"),
        ).where(Attendance.school_id == school_id)
        if f.from_date:
            tq = tq.where(Attendance.attended_on >= f.from_date)
        if f.to_date:
            tq = tq.where(Attendance.attended_on <= f.to_date)
        if f.section_id:
            tq = tq.where(Attendance.class_id == f.section_id)
        trend_rows = (await self.db.execute(tq.group_by("m").order_by("m"))).all()
        trend = []
        for m, tot, att in trend_rows:
            label = m.strftime("%b %Y") if hasattr(m, "strftime") else str(m)[:7]
            trend.append(MetricPoint(label=label, value=_pct(att or 0, tot or 0)))

        # By grade via class → grade
        gq = (
            select(Grade.name, func.count(Attendance.id), func.sum(
                case(
                    (
                        Attendance.status.in_(
                            [
                                AttendanceStatus.PRESENT.value,
                                AttendanceStatus.LATE.value,
                                AttendanceStatus.HALF_DAY.value,
                            ]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ))
            .join(ClassRoom, ClassRoom.id == Attendance.class_id)
            .join(Grade, Grade.id == ClassRoom.grade_id)
            .where(Attendance.school_id == school_id)
            .group_by(Grade.name)
            .order_by(Grade.name)
        )
        if f.from_date:
            gq = gq.where(Attendance.attended_on >= f.from_date)
        if f.to_date:
            gq = gq.where(Attendance.attended_on <= f.to_date)
        by_grade = [
            MetricPoint(label=n or "—", value=_pct(a or 0, t or 0))
            for n, t, a in (await self.db.execute(gq)).all()
        ]

        sq = (
            select(ClassRoom.name, ClassRoom.section, func.count(Attendance.id), func.sum(
                case(
                    (
                        Attendance.status.in_(
                            [
                                AttendanceStatus.PRESENT.value,
                                AttendanceStatus.LATE.value,
                                AttendanceStatus.HALF_DAY.value,
                            ]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ))
            .join(ClassRoom, ClassRoom.id == Attendance.class_id)
            .where(Attendance.school_id == school_id)
            .group_by(ClassRoom.name, ClassRoom.section)
            .order_by(ClassRoom.name)
        )
        if f.grade_id:
            sq = sq.where(ClassRoom.grade_id == f.grade_id)
        by_section = [
            MetricPoint(label=f"{n}{('-' + s) if s else ''}", value=_pct(a or 0, t or 0))
            for n, s, t, a in (await self.db.execute(sq)).all()
        ]

        settings = await self.get_settings(school_id)
        needs = await self._attendance_needs_attention(school_id, f, settings.attendance_threshold)

        return AttendanceAnalyticsOut(
            attendance_rate=rate,
            present=present,
            absent=absent,
            late=late,
            excused=excused,
            half_day=half,
            total=total,
            trend=trend,
            by_grade=by_grade,
            by_section=by_section,
            needs_attention=needs,
        )

    async def _attendance_needs_attention(
        self, school_id: UUID, f: AnalyticsFilters, threshold: float
    ) -> list[AttentionItem]:
        q = (
            select(
                Attendance.student_id,
                Attendance.class_id,
                func.count().label("total"),
                func.sum(
                    case(
                        (
                            Attendance.status.in_(
                                [
                                    AttendanceStatus.PRESENT.value,
                                    AttendanceStatus.LATE.value,
                                    AttendanceStatus.HALF_DAY.value,
                                ]
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("att"),
            )
            .where(Attendance.school_id == school_id)
            .group_by(Attendance.student_id, Attendance.class_id)
        )
        if f.from_date:
            q = q.where(Attendance.attended_on >= f.from_date)
        if f.to_date:
            q = q.where(Attendance.attended_on <= f.to_date)
        if f.section_id:
            q = q.where(Attendance.class_id == f.section_id)
        out: list[AttentionItem] = []
        for sid, cid, tot, att in (await self.db.execute(q)).all():
            rate = _pct(att or 0, tot or 0)
            if tot and rate < threshold:
                out.append(
                    AttentionItem(
                        student_id=sid,
                        student_name=await self._student_name(sid),
                        class_name=await self._class_name(cid),
                        indicator="Below Threshold",
                        detail=f"Attendance {rate}% (threshold {threshold}%)",
                        value=rate,
                    )
                )
        out.sort(key=lambda x: x.value or 0)
        return out[:50]

    async def homework(self, school_id: UUID, f: AnalyticsFilters) -> HomeworkAnalyticsOut:
        hq = select(Homework).where(Homework.school_id == school_id)
        if f.section_id:
            hq = hq.where(Homework.class_id == f.section_id)
        if f.subject_id:
            hq = hq.where(Homework.subject_id == f.subject_id)
        if f.academic_year_id:
            hq = hq.where(Homework.academic_year_id == f.academic_year_id)
        homeworks = (await self.db.execute(hq)).scalars().all()
        total = len(homeworks)
        published = sum(1 for h in homeworks if h.status == HomeworkStatus.PUBLISHED.value)
        hw_ids = [h.id for h in homeworks]
        if not hw_ids:
            return HomeworkAnalyticsOut(
                total_assignments=0,
                published=0,
                submissions=0,
                late=0,
                missing=0,
                graded=0,
                submission_rate=0,
                on_time_rate=0,
                late_rate=0,
                missing_rate=0,
            )

        subs = (
            await self.db.execute(
                select(HomeworkSubmission).where(HomeworkSubmission.homework_id.in_(hw_ids))
            )
        ).scalars().all()
        submissions = len(subs)
        late = sum(1 for s in subs if s.status == HomeworkSubmissionStatus.LATE.value)
        graded = sum(
            1
            for s in subs
            if s.status
            in (
                HomeworkSubmissionStatus.GRADED.value,
                HomeworkSubmissionStatus.RETURNED.value,
            )
        )
        # Expected submissions ≈ published * active students in those classes (approx via enrollment)
        class_ids = list({h.class_id for h in homeworks if h.status != HomeworkStatus.DRAFT.value})
        expected = 0
        if class_ids:
            expected = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Enrollment)
                    .where(
                        Enrollment.school_id == school_id,
                        Enrollment.class_id.in_(class_ids),
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalar_one()
            # rough: each published homework expects expected/len(unique classes) per class
            # Better: count per homework
            expected = 0
            for h in homeworks:
                if h.status == HomeworkStatus.DRAFT.value:
                    continue
                c = (
                    await self.db.execute(
                        select(func.count())
                        .select_from(Enrollment)
                        .where(
                            Enrollment.school_id == school_id,
                            Enrollment.class_id == h.class_id,
                            Enrollment.status == EnrollmentStatus.ACTIVE.value,
                        )
                    )
                ).scalar_one()
                expected += int(c or 0)
        missing = max(expected - submissions, 0)
        on_time = max(submissions - late, 0)
        sub_rate = _pct(submissions, expected) if expected else _pct(submissions, submissions or 1)
        # By class
        by_class_map: dict[str, list[int]] = {}
        for h in homeworks:
            if h.status == HomeworkStatus.DRAFT.value:
                continue
            cname = await self._class_name(h.class_id) or "—"
            hsubs = [s for s in subs if s.homework_id == h.id]
            enr_c = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Enrollment)
                    .where(
                        Enrollment.class_id == h.class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalar_one()
            by_class_map.setdefault(cname, [0, 0])
            by_class_map[cname][0] += len(hsubs)
            by_class_map[cname][1] += int(enr_c or 0)
        by_class = [
            MetricPoint(label=k, value=_pct(v[0], v[1])) for k, v in sorted(by_class_map.items())
        ]
        # By subject
        by_subj_map: dict[str, list[int]] = {}
        for h in homeworks:
            if h.status == HomeworkStatus.DRAFT.value:
                continue
            subj = await self.db.get(Subject, h.subject_id)
            sname = subj.name if subj else "—"
            hsubs = [s for s in subs if s.homework_id == h.id]
            enr_c = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Enrollment)
                    .where(
                        Enrollment.class_id == h.class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalar_one()
            by_subj_map.setdefault(sname, [0, 0])
            by_subj_map[sname][0] += len(hsubs)
            by_subj_map[sname][1] += int(enr_c or 0)
        by_subject = [
            MetricPoint(label=k, value=_pct(v[0], v[1])) for k, v in sorted(by_subj_map.items())
        ]
        return HomeworkAnalyticsOut(
            total_assignments=total,
            published=published,
            submissions=submissions,
            late=late,
            missing=missing,
            graded=graded,
            submission_rate=sub_rate,
            on_time_rate=_pct(on_time, submissions) if submissions else 0,
            late_rate=_pct(late, submissions) if submissions else 0,
            missing_rate=_pct(missing, expected) if expected else 0,
            by_class=by_class,
            by_subject=by_subject,
        )

    async def exams(self, school_id: UUID, f: AnalyticsFilters) -> ExamAnalyticsOut:
        q = select(Result).where(
            Result.school_id == school_id,
            Result.is_published.is_(True),
        )
        if f.exam_id:
            q = q.where(Result.exam_id == f.exam_id)
        if f.section_id:
            q = q.where(Result.class_id == f.section_id)
        results = (await self.db.execute(q)).scalars().all()
        # Filter by academic year via exam
        if f.academic_year_id or f.grade_id:
            filtered = []
            for r in results:
                exam = await self.db.get(Exam, r.exam_id)
                if not exam:
                    continue
                if f.academic_year_id and exam.academic_year_id != f.academic_year_id:
                    continue
                if f.grade_id and getattr(exam, "grade_id", None) and exam.grade_id != f.grade_id:
                    continue
                filtered.append(r)
            results = filtered

        pcts = [float(r.percentage) for r in results if r.percentage is not None]
        passed = sum(1 for r in results if (r.pass_fail or "").upper() == "PASS")
        failed = sum(1 for r in results if (r.pass_fail or "").upper() == "FAIL")
        scored = passed + failed

        # Subject averages from student marks for published exams
        exam_ids = list({r.exam_id for r in results})
        by_subject: list[MetricPoint] = []
        if exam_ids:
            marks = (
                await self.db.execute(
                    select(ExamSubject.subject_id, func.avg(StudentMark.obtained_marks))
                    .join(StudentMark, StudentMark.exam_subject_id == ExamSubject.id)
                    .where(
                        ExamSubject.exam_id.in_(exam_ids),
                        StudentMark.obtained_marks.is_not(None),
                    )
                    .group_by(ExamSubject.subject_id)
                )
            ).all()
            for sid, avg in marks:
                subj = await self.db.get(Subject, sid)
                # Convert to approx % if we have max — skip raw marks average as percentage-ish
                by_subject.append(
                    MetricPoint(label=subj.name if subj else "—", value=round(float(avg or 0), 1))
                )

        # Exam trend by exam title
        exam_trend: list[MetricPoint] = []
        by_exam: dict[UUID, list[float]] = {}
        for r in results:
            if r.percentage is not None:
                by_exam.setdefault(r.exam_id, []).append(float(r.percentage))
        for eid, vals in by_exam.items():
            exam = await self.db.get(Exam, eid)
            exam_trend.append(
                MetricPoint(
                    label=exam.title if exam else str(eid)[:8],
                    value=round(sum(vals) / len(vals), 1),
                )
            )

        return ExamAnalyticsOut(
            students=len({r.student_id for r in results}),
            average=round(sum(pcts) / len(pcts), 1) if pcts else None,
            median=round(float(median(pcts)), 1) if pcts else None,
            highest=max(pcts) if pcts else None,
            lowest=min(pcts) if pcts else None,
            pass_rate=_pct(passed, scored) if scored else None,
            fail_rate=_pct(failed, scored) if scored else None,
            by_subject=by_subject,
            exam_trend=exam_trend,
        )

    async def fees(self, school_id: UUID, f: AnalyticsFilters) -> FeesAnalyticsOut:
        q = select(FeeInvoice).where(
            FeeInvoice.school_id == school_id,
            FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
        )
        invoices = (await self.db.execute(q)).scalars().all()
        if f.academic_year_id or f.section_id or f.grade_id:
            filtered = []
            for inv in invoices:
                acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
                if not acc:
                    continue
                if f.academic_year_id and acc.academic_year_id != f.academic_year_id:
                    continue
                if f.section_id or f.grade_id:
                    enr = (
                        await self.db.execute(
                            select(Enrollment).where(
                                Enrollment.student_id == acc.student_id,
                                Enrollment.academic_year_id == acc.academic_year_id,
                                Enrollment.status == EnrollmentStatus.ACTIVE.value,
                            )
                        )
                    ).scalar_one_or_none()
                    if not enr:
                        continue
                    if f.section_id and enr.class_id != f.section_id:
                        continue
                    if f.grade_id and enr.grade_id != f.grade_id:
                        continue
                filtered.append(inv)
            invoices = filtered

        assessed = sum(float(i.total_amount or 0) for i in invoices)
        collected = sum(float(i.paid_amount or 0) for i in invoices)
        outstanding = sum(float(i.balance_amount or 0) for i in invoices)
        overdue = sum(
            float(i.balance_amount or 0)
            for i in invoices
            if float(i.balance_amount or 0) > 0 and i.due_date < date.today()
        )

        pays = (
            await self.db.execute(
                select(Payment).where(
                    Payment.school_id == school_id,
                    Payment.status == PaymentStatus.SUCCESS.value,
                )
            )
        ).scalars().all()
        month_map: dict[str, float] = {}
        for p in pays:
            d = (p.paid_at or p.created_at).date()
            if f.from_date and d < f.from_date:
                continue
            if f.to_date and d > f.to_date:
                continue
            key = d.strftime("%b %Y")
            month_map[key] = month_map.get(key, 0) + float(p.amount or 0)
        trend = [MetricPoint(label=k, value=round(v, 2)) for k, v in month_map.items()]

        # By grade (assessed)
        grade_map: dict[str, float] = {}
        for inv in invoices:
            acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
            if not acc:
                continue
            enr = (
                await self.db.execute(
                    select(Enrollment).where(
                        Enrollment.student_id == acc.student_id,
                        Enrollment.academic_year_id == acc.academic_year_id,
                    )
                )
            ).scalar_one_or_none()
            gname = "—"
            if enr and enr.grade_id:
                g = await self.db.get(Grade, enr.grade_id)
                gname = g.name if g else "—"
            grade_map[gname] = grade_map.get(gname, 0) + float(inv.paid_amount or 0)
        by_grade = [MetricPoint(label=k, value=round(v, 2)) for k, v in sorted(grade_map.items())]

        return FeesAnalyticsOut(
            currency="INR",
            assessed=round(assessed, 2),
            collected=round(collected, 2),
            outstanding=round(outstanding, 2),
            overdue=round(overdue, 2),
            collection_rate=_pct(collected, assessed),
            trend=trend,
            by_grade=by_grade,
        )

    async def communication(self, school_id: UUID, f: AnalyticsFilters) -> CommunicationAnalyticsOut:
        announcements = (
            await self.db.execute(
                select(func.count())
                .select_from(Announcement)
                .where(Announcement.school_id == school_id)
            )
        ).scalar_one()
        notif_sent = (
            await self.db.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.school_id == school_id)
            )
        ).scalar_one()
        notif_read = (
            await self.db.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.school_id == school_id, Notification.is_read.is_(True))
            )
        ).scalar_one()
        messages = (
            await self.db.execute(
                select(func.count())
                .select_from(ConversationMessage)
                .where(ConversationMessage.school_id == school_id)
            )
        ).scalar_one()
        unread = int(notif_sent or 0) - int(notif_read or 0)
        return CommunicationAnalyticsOut(
            announcements_sent=int(announcements or 0),
            notifications_sent=int(notif_sent or 0),
            notifications_read=int(notif_read or 0),
            notifications_unread=max(unread, 0),
            messages_sent=int(messages or 0),
            unread_messages=0,
        )

    async def classes(self, school_id: UUID, f: AnalyticsFilters) -> list[ClassAnalyticsOut]:
        cq = select(ClassRoom).where(ClassRoom.school_id == school_id)
        if f.grade_id:
            cq = cq.where(ClassRoom.grade_id == f.grade_id)
        if f.section_id:
            cq = cq.where(ClassRoom.id == f.section_id)
        rooms = (await self.db.execute(cq.order_by(ClassRoom.name))).scalars().all()
        out: list[ClassAnalyticsOut] = []
        for room in rooms:
            sf = AnalyticsFilters(
                academic_year_id=f.academic_year_id,
                grade_id=f.grade_id,
                section_id=room.id,
                from_date=f.from_date,
                to_date=f.to_date,
            )
            students = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Enrollment)
                    .where(
                        Enrollment.class_id == room.id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                        *(
                            [Enrollment.academic_year_id == f.academic_year_id]
                            if f.academic_year_id
                            else []
                        ),
                    )
                )
            ).scalar_one()
            att = await self.attendance(school_id, sf)
            hw = await self.homework(school_id, sf)
            ex = await self.exams(school_id, sf)
            fees = await self.fees(school_id, sf)
            out.append(
                ClassAnalyticsOut(
                    section_id=room.id,
                    class_name=f"{room.name}{('-' + room.section) if room.section else ''}",
                    students=int(students or 0),
                    attendance_rate=att.attendance_rate,
                    homework_completion=hw.submission_rate,
                    exam_average=ex.average,
                    fee_collection_rate=fees.collection_rate,
                )
            )
        return out

    async def class_detail(
        self, school_id: UUID, section_id: UUID, f: AnalyticsFilters
    ) -> ClassAnalyticsOut:
        f2 = f.model_copy(update={"section_id": section_id})
        rows = await self.classes(school_id, f2)
        if not rows:
            raise NotFoundError("Class not found")
        return rows[0]

    async def student(
        self, school_id: UUID, student_id: UUID, f: AnalyticsFilters
    ) -> StudentAnalyticsOut:
        st = await self.db.get(Student, student_id)
        if not st or st.school_id != school_id:
            raise NotFoundError("Student not found")
        enr_q = select(Enrollment).where(
            Enrollment.school_id == school_id,
            Enrollment.student_id == student_id,
        )
        if f.academic_year_id:
            enr_q = enr_q.where(Enrollment.academic_year_id == f.academic_year_id)
        else:
            enr_q = enr_q.where(Enrollment.status == EnrollmentStatus.ACTIVE.value)
        enr = (await self.db.execute(enr_q.order_by(Enrollment.enrolled_at.desc()))).scalars().first()
        section_id = enr.class_id if enr else None
        sf = AnalyticsFilters(
            academic_year_id=f.academic_year_id or (enr.academic_year_id if enr else None),
            section_id=section_id,
            from_date=f.from_date,
            to_date=f.to_date,
        )

        # Attendance rate for this student
        aq = select(
            func.count(),
            func.sum(
                case(
                    (
                        Attendance.status.in_(
                            [
                                AttendanceStatus.PRESENT.value,
                                AttendanceStatus.LATE.value,
                                AttendanceStatus.HALF_DAY.value,
                            ]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        ).where(Attendance.school_id == school_id, Attendance.student_id == student_id)
        if f.from_date:
            aq = aq.where(Attendance.attended_on >= f.from_date)
        if f.to_date:
            aq = aq.where(Attendance.attended_on <= f.to_date)
        tot, att = (await self.db.execute(aq)).one()
        att_rate = _pct(att or 0, tot or 0) if tot else None

        # Homework
        if section_id:
            hw_ids = (
                await self.db.execute(
                    select(Homework.id).where(
                        Homework.school_id == school_id,
                        Homework.class_id == section_id,
                        Homework.status != HomeworkStatus.DRAFT.value,
                    )
                )
            ).scalars().all()
            if hw_ids:
                submitted = (
                    await self.db.execute(
                        select(func.count())
                        .select_from(HomeworkSubmission)
                        .where(
                            HomeworkSubmission.homework_id.in_(hw_ids),
                            HomeworkSubmission.student_id == student_id,
                        )
                    )
                ).scalar_one()
                hw_rate = _pct(submitted or 0, len(hw_ids))
            else:
                hw_rate = None
        else:
            hw_rate = None

        results = (
            await self.db.execute(
                select(Result).where(
                    Result.school_id == school_id,
                    Result.student_id == student_id,
                    Result.is_published.is_(True),
                )
            )
        ).scalars().all()
        exam_trend = []
        latest = None
        for r in results:
            if r.percentage is None:
                continue
            exam = await self.db.get(Exam, r.exam_id)
            exam_trend.append(
                MetricPoint(label=exam.title if exam else "Exam", value=float(r.percentage))
            )
            latest = float(r.percentage)
        subject_scores: list[MetricPoint] = []
        if results:
            latest_exam = results[-1]
            marks = (
                await self.db.execute(
                    select(ExamSubject.subject_id, StudentMark.obtained_marks, StudentMark.max_marks)
                    .join(StudentMark, StudentMark.exam_subject_id == ExamSubject.id)
                    .where(
                        ExamSubject.exam_id == latest_exam.exam_id,
                        StudentMark.student_id == student_id,
                    )
                )
            ).all()
            for sid, obtained, max_m in marks:
                subj = await self.db.get(Subject, sid)
                if obtained is None:
                    continue
                pct = _pct(obtained, max_m) if max_m else float(obtained)
                subject_scores.append(MetricPoint(label=subj.name if subj else "—", value=pct))

        fees_out = None
        accs = (
            await self.db.execute(
                select(StudentFeeAccount).where(
                    StudentFeeAccount.school_id == school_id,
                    StudentFeeAccount.student_id == student_id,
                )
            )
        ).scalars().all()
        if accs:
            invs = (
                await self.db.execute(
                    select(FeeInvoice).where(
                        FeeInvoice.student_fee_account_id.in_([a.id for a in accs]),
                        FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                    )
                )
            ).scalars().all()
            fees_out = round(sum(float(i.balance_amount or 0) for i in invs), 2)

        return StudentAnalyticsOut(
            student_id=student_id,
            student_name=await self._student_name(student_id),
            class_name=await self._class_name(section_id),
            attendance_rate=att_rate,
            homework_completion=hw_rate,
            latest_exam_percentage=latest,
            fees_outstanding=fees_out,
            exam_trend=exam_trend,
            subject_scores=subject_scores,
            attendance_trend=[],
        )

    async def teacher(
        self, school_id: UUID, user_id: UUID, f: AnalyticsFilters
    ) -> TeacherAnalyticsOut:
        class_ids = await self._teacher_class_ids(school_id, user_id)
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        classes_out: list[ClassAnalyticsOut] = []
        for cid in class_ids:
            try:
                classes_out.append(
                    await self.class_detail(
                        school_id, cid, f.model_copy(update={"section_id": cid})
                    )
                )
            except NotFoundError:
                continue
        created = 0
        graded = 0
        if teacher:
            created = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Homework)
                    .where(Homework.school_id == school_id, Homework.teacher_id == teacher.id)
                )
            ).scalar_one()
            graded = (
                await self.db.execute(
                    select(func.count())
                    .select_from(HomeworkSubmission)
                    .join(Homework, Homework.id == HomeworkSubmission.homework_id)
                    .where(
                        Homework.teacher_id == teacher.id,
                        HomeworkSubmission.status.in_(
                            [
                                HomeworkSubmissionStatus.GRADED.value,
                                HomeworkSubmissionStatus.RETURNED.value,
                            ]
                        ),
                    )
                )
            ).scalar_one()
        marked = (
            await self.db.execute(
                select(func.count(func.distinct(Attendance.attended_on))).where(
                    Attendance.school_id == school_id,
                    Attendance.class_id.in_(class_ids or [uuid4()]),
                )
            )
        ).scalar_one()
        students = (
            await self.db.execute(
                select(func.count(func.distinct(Enrollment.student_id))).where(
                    Enrollment.school_id == school_id,
                    Enrollment.class_id.in_(class_ids or [uuid4()]),
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalar_one()
        return TeacherAnalyticsOut(
            classes=classes_out,
            assignments_created=int(created or 0),
            assignments_graded=int(graded or 0),
            attendance_marked_days=int(marked or 0),
            students_taught=int(students or 0),
        )

    async def attention_items(
        self, school_id: UUID, f: AnalyticsFilters
    ) -> list[AttentionItem]:
        settings = await self.get_settings(school_id)
        items = await self._attendance_needs_attention(
            school_id, f, settings.attendance_threshold
        )
        # Overdue fees
        invs = (
            await self.db.execute(
                select(FeeInvoice).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.due_date < date.today(),
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
            )
        ).scalars().all()
        seen: set[UUID] = set()
        for inv in invs[:30]:
            acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
            if not acc or acc.student_id in seen:
                continue
            seen.add(acc.student_id)
            items.append(
                AttentionItem(
                    student_id=acc.student_id,
                    student_name=await self._student_name(acc.student_id),
                    class_name=None,
                    indicator="Needs Attention",
                    detail=f"Fees overdue · balance {inv.currency} {inv.balance_amount}",
                    value=float(inv.balance_amount),
                )
            )
        return items
