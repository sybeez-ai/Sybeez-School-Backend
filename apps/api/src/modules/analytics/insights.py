"""Live school-admin insights: dashboard, progress, heatmap.

All aggregates come from attendance, written and online assessments,
homework, fees, enrolment and teachers. Unmarked attendance is never
treated as 100%.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models import (
    AcademicYear,
    Attendance,
    ClassSubject,
    Enrollment,
    ExamSubject,
    FeeInvoice,
    Homework,
    HomeworkSubmission,
    OnlineTest,
    OnlineTestAttempt,
    Student,
    StudentFeeAccount,
    StudentMark,
    Subject,
    Teacher,
    TimetableSettings,
    User,
)
from src.db.models.class_ import ClassRoom
from src.modules.analytics.insights_schemas import (
    ClassProgressRow,
    ClassRank,
    HeatmapAxis,
    HeatmapCell,
    HeatmapDrillStudent,
    HeatmapLegendItem,
    InsightsDashboardOut,
    InsightsHeatmapDrillOut,
    InsightsHeatmapOut,
    InsightsProgressOut,
    KpiValue,
    ReportHistoryItem,
    StudentInsightOut,
    StudentProgressRow,
    SubjectScore,
    TrendWeek,
)
from src.modules.analytics.rates import (
    ATTENDANCE_RISK_THRESHOLD,
    DEFAULT_LOOKBACK_DAYS,
    FOCUS_THRESHOLD,
    attendance_rate,
    iso_week_label,
    kpi_delta,
    monday_of,
    optional_rate,
    parse_working_days,
    previous_period,
    score_band,
    term_bounds,
    working_dates,
)
from src.modules.analytics.schemas import AnalyticsFilters, MetricPoint
from src.modules.reports.schemas import REPORT_CATALOG
from src.db.models.reporting import ReportAuditLog
from src.shared.enums import (
    AttendanceStatus,
    EnrollmentStatus,
    FeeInvoiceStatus,
    HomeworkStatus,
    HomeworkSubmissionStatus,
    MarkEntryStatus,
    PersonStatus,
)
from src.shared.utils import format_class_label

ATTENDING = {
    AttendanceStatus.PRESENT.value,
    AttendanceStatus.LATE.value,
    AttendanceStatus.HALF_DAY.value,
}
COUNTED_MARKS = {MarkEntryStatus.SUBMITTED.value, MarkEntryStatus.LOCKED.value}
GRADED_HW = {
    HomeworkSubmissionStatus.SUBMITTED.value,
    HomeworkSubmissionStatus.LATE.value,
    HomeworkSubmissionStatus.GRADED.value,
    HomeworkSubmissionStatus.RETURNED.value,
}
GRADE_BUCKETS = [
    ("0–34", 0, 35),
    ("35–49", 35, 50),
    ("50–74", 50, 75),
    ("75–89", 75, 90),
    ("90–100", 90, 101),
]
HEATMAP_LEGEND = [
    HeatmapLegendItem(band="needs-work", label="Below 50%", min=0, max=49.9),
    HeatmapLegendItem(band="ok", label="50–74%", min=50, max=74.9),
    HeatmapLegendItem(band="strong", label="75% and above", min=75, max=100),
    HeatmapLegendItem(band="no-data", label="Not marked", min=None, max=None),
]


@dataclass
class ScoreEvent:
    student_id: UUID
    class_id: UUID | None
    subject_id: UUID | None
    on: date
    percent: float
    passed: bool | None
    source: str


@dataclass
class Scope:
    start: date
    end: date
    prev_start: date
    prev_end: date
    weekdays: set[int]
    days: list[date]
    rooms: list[ClassRoom] = field(default_factory=list)
    enrollments: list[Enrollment] = field(default_factory=list)
    class_ids: set[UUID] = field(default_factory=set)
    student_ids: set[UUID] = field(default_factory=set)
    class_students: dict[UUID, set[UUID]] = field(default_factory=lambda: defaultdict(set))
    student_class: dict[UUID, UUID] = field(default_factory=dict)
    class_names: dict[UUID, str] = field(default_factory=dict)
    student_names: dict[UUID, str] = field(default_factory=dict)
    subjects: dict[UUID, Subject] = field(default_factory=dict)
    class_subjects: dict[UUID, set[UUID]] = field(default_factory=lambda: defaultdict(set))


class InsightsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def weekdays_for(self, school_id: UUID) -> set[int]:
        row = (
            await self.db.execute(
                select(TimetableSettings).where(TimetableSettings.school_id == school_id)
            )
        ).scalar_one_or_none()
        return parse_working_days(row.working_days if row else None)

    async def resolve_window(self, school_id: UUID, f: AnalyticsFilters) -> tuple[date, date, date, date]:
        today = date.today()
        year = None
        if f.academic_year_id:
            year = await self.db.get(AcademicYear, f.academic_year_id)
        start = f.from_date
        end = f.to_date or today
        term = getattr(f, "term", None)
        if year is not None:
            bounds = term_bounds(year.starts_on, year.ends_on, term)
            if bounds:
                t0, t1 = bounds
                start = start or t0
                end = min(end, t1)
            if start is None:
                start = max(year.starts_on, today - timedelta(days=DEFAULT_LOOKBACK_DAYS))
            start = max(start, year.starts_on)
            end = min(end, min(year.ends_on, today))
        if start is None:
            start = today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        end = min(end, today)
        if start > end:
            start = end
        prev_start, prev_end = previous_period(start, end)
        return start, end, prev_start, prev_end

    async def _load_scope(self, school_id: UUID, f: AnalyticsFilters, start: date, end: date) -> Scope:
        weekdays = await self.weekdays_for(school_id)
        prev_start, prev_end = previous_period(start, end)
        rq = select(ClassRoom).where(ClassRoom.school_id == school_id)
        if f.grade_id:
            rq = rq.where(ClassRoom.grade_id == f.grade_id)
        if f.section_id:
            rq = rq.where(ClassRoom.id == f.section_id)
        if f.academic_year_id:
            rq = rq.where(ClassRoom.academic_year_id == f.academic_year_id)
        rooms = (await self.db.execute(rq.order_by(ClassRoom.name, ClassRoom.section))).scalars().all()
        class_ids = {r.id for r in rooms}

        eq = select(Enrollment).where(
            Enrollment.school_id == school_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
        )
        if f.academic_year_id:
            eq = eq.where(Enrollment.academic_year_id == f.academic_year_id)
        if f.grade_id:
            eq = eq.where(Enrollment.grade_id == f.grade_id)
        if class_ids:
            eq = eq.where(Enrollment.class_id.in_(class_ids))
        enrollments = (await self.db.execute(eq)).scalars().all()

        class_students: dict[UUID, set[UUID]] = defaultdict(set)
        student_class: dict[UUID, UUID] = {}
        student_ids: set[UUID] = set()
        for enr in enrollments:
            class_students[enr.class_id].add(enr.student_id)
            student_class[enr.student_id] = enr.class_id
            student_ids.add(enr.student_id)

        class_names = {r.id: format_class_label(r.name, r.section) for r in rooms}

        names: dict[UUID, str] = {}
        if student_ids:
            rows = (
                await self.db.execute(
                    select(Student.id, User.full_name)
                    .join(User, User.id == Student.user_id)
                    .where(Student.id.in_(student_ids))
                )
            ).all()
            names = {sid: (full or "Student") for sid, full in rows}

        subjects = {
            s.id: s
            for s in (await self.db.execute(select(Subject).where(Subject.school_id == school_id))).scalars().all()
        }
        class_subjects: dict[UUID, set[UUID]] = defaultdict(set)
        if class_ids:
            cs_rows = (
                await self.db.execute(
                    select(ClassSubject).where(
                        ClassSubject.school_id == school_id,
                        ClassSubject.class_id.in_(class_ids),
                    )
                )
            ).scalars().all()
            for row in cs_rows:
                class_subjects[row.class_id].add(row.subject_id)

        return Scope(
            start=start,
            end=end,
            prev_start=prev_start,
            prev_end=prev_end,
            weekdays=weekdays,
            days=working_dates(start, end, weekdays),
            rooms=rooms,
            enrollments=enrollments,
            class_ids=class_ids,
            student_ids=student_ids,
            class_students=class_students,
            student_class=student_class,
            class_names=class_names,
            student_names=names,
            subjects=subjects,
            class_subjects=class_subjects,
        )

    def _expected_for(self, scope: Scope, class_id: UUID | None = None, days: list[date] | None = None) -> int:
        days = days if days is not None else scope.days
        if class_id is not None:
            return len(scope.class_students.get(class_id, ())) * len(days)
        return sum(len(sids) * len(days) for sids in scope.class_students.values())

    async def _attendance_rows(
        self,
        school_id: UUID,
        scope: Scope,
        start: date,
        end: date,
        *,
        class_id: UUID | None = None,
        student_id: UUID | None = None,
    ) -> list[Attendance]:
        q = select(Attendance).where(
            Attendance.school_id == school_id,
            Attendance.attended_on >= start,
            Attendance.attended_on <= end,
        )
        if class_id:
            q = q.where(Attendance.class_id == class_id)
        elif scope.class_ids:
            q = q.where(Attendance.class_id.in_(scope.class_ids))
        if student_id:
            q = q.where(Attendance.student_id == student_id)
        elif scope.student_ids:
            q = q.where(Attendance.student_id.in_(scope.student_ids))
        return list((await self.db.execute(q)).scalars().all())

    def _summarize_attendance(
        self,
        rows: list[Attendance],
        expected: int,
        *,
        start: date | None = None,
        end: date | None = None,
        class_id: UUID | None = None,
        student_id: UUID | None = None,
        weekdays: set[int] | None = None,
    ) -> tuple[float | None, int, int, int]:
        filtered = []
        for a in rows:
            if start and a.attended_on < start:
                continue
            if end and a.attended_on > end:
                continue
            if class_id and a.class_id != class_id:
                continue
            if student_id and a.student_id != student_id:
                continue
            if weekdays is not None and a.attended_on.weekday() not in weekdays:
                continue
            filtered.append(a)
        marked = len(filtered)
        attending = sum(1 for a in filtered if a.status in ATTENDING)
        unmarked = max(0, expected - marked)
        return attendance_rate(attending=attending, marked=marked, expected=expected), marked, expected, unmarked

    async def _score_events(
        self,
        school_id: UUID,
        scope: Scope,
        start: date,
        end: date,
        *,
        subject_id: UUID | None = None,
        student_id: UUID | None = None,
    ) -> list[ScoreEvent]:
        events: list[ScoreEvent] = []
        mq = (
            select(
                StudentMark.student_id,
                StudentMark.class_id,
                ExamSubject.subject_id,
                ExamSubject.exam_date,
                ExamSubject.pass_marks,
                StudentMark.obtained_marks,
                StudentMark.max_marks,
                StudentMark.entered_at,
            )
            .join(ExamSubject, ExamSubject.id == StudentMark.exam_subject_id)
            .where(
                StudentMark.school_id == school_id,
                StudentMark.status.in_(COUNTED_MARKS),
                StudentMark.obtained_marks.is_not(None),
            )
        )
        if scope.student_ids:
            mq = mq.where(StudentMark.student_id.in_(scope.student_ids))
        if student_id:
            mq = mq.where(StudentMark.student_id == student_id)
        if subject_id:
            mq = mq.where(ExamSubject.subject_id == subject_id)
        for sid, cid, subj, exam_date, pass_marks, obtained, max_marks, entered_at in (
            await self.db.execute(mq)
        ).all():
            on = exam_date or (entered_at.date() if entered_at else None)
            if on is None or on < start or on > end:
                continue
            mx = float(max_marks or 0) or 0.0
            if mx <= 0:
                continue
            pct = float(obtained) / mx * 100
            passed = None
            if pass_marks is not None:
                passed = float(obtained) >= float(pass_marks)
            events.append(
                ScoreEvent(
                    student_id=sid,
                    class_id=cid,
                    subject_id=subj,
                    on=on,
                    percent=pct,
                    passed=passed,
                    source="exam",
                )
            )

        aq = (
            select(
                OnlineTestAttempt.student_id,
                OnlineTestAttempt.class_id,
                OnlineTest.subject_id,
                OnlineTestAttempt.submitted_at,
                OnlineTestAttempt.score,
                OnlineTestAttempt.max_score,
                OnlineTestAttempt.percentage,
            )
            .join(OnlineTest, OnlineTest.id == OnlineTestAttempt.test_id)
            .where(
                OnlineTestAttempt.school_id == school_id,
                OnlineTestAttempt.submitted_at.is_not(None),
                OnlineTestAttempt.score.is_not(None),
            )
        )
        if scope.student_ids:
            aq = aq.where(OnlineTestAttempt.student_id.in_(scope.student_ids))
        if student_id:
            aq = aq.where(OnlineTestAttempt.student_id == student_id)
        if subject_id:
            aq = aq.where(OnlineTest.subject_id == subject_id)
        for sid, cid, subj, submitted_at, score, max_score, percentage in (await self.db.execute(aq)).all():
            on = submitted_at.date() if submitted_at else None
            if on is None or on < start or on > end:
                continue
            if percentage is not None:
                pct = float(percentage)
            else:
                mx = float(max_score or 0)
                if mx <= 0:
                    continue
                pct = float(score) / mx * 100
            passed = pct >= FOCUS_THRESHOLD
            events.append(
                ScoreEvent(
                    student_id=sid,
                    class_id=cid,
                    subject_id=subj,
                    on=on,
                    percent=pct,
                    passed=passed,
                    source="online",
                )
            )
        return events

    def _avg(self, events: list[ScoreEvent]) -> float | None:
        if not events:
            return None
        return round(sum(e.percent for e in events) / len(events), 1)

    def _pass_rate(self, events: list[ScoreEvent]) -> float | None:
        decided = [e for e in events if e.passed is not None]
        if not decided:
            return None
        return optional_rate(sum(1 for e in decided if e.passed), len(decided))

    async def _fee_rates(
        self,
        school_id: UUID,
        scope: Scope,
        start: date,
        end: date,
        *,
        student_id: UUID | None = None,
    ) -> tuple[float | None, float, float]:
        q = (
            select(FeeInvoice)
            .join(StudentFeeAccount, StudentFeeAccount.id == FeeInvoice.student_fee_account_id)
            .where(
                FeeInvoice.school_id == school_id,
                FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                FeeInvoice.invoice_date >= start,
                FeeInvoice.invoice_date <= end,
            )
        )
        if scope.student_ids:
            q = q.where(StudentFeeAccount.student_id.in_(scope.student_ids))
        if student_id:
            q = q.where(StudentFeeAccount.student_id == student_id)
        invoices = (await self.db.execute(q)).scalars().all()
        assessed = sum(float(i.total_amount or 0) for i in invoices)
        collected = sum(float(i.paid_amount or 0) for i in invoices)
        return optional_rate(collected, assessed), collected, assessed

    async def _homework_rate(
        self,
        school_id: UUID,
        scope: Scope,
        start: date,
        end: date,
        *,
        student_id: UUID | None = None,
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
    ) -> float | None:
        hq = select(Homework).where(
            Homework.school_id == school_id,
            Homework.status != HomeworkStatus.DRAFT.value,
        )
        if scope.class_ids:
            hq = hq.where(Homework.class_id.in_(scope.class_ids))
        if class_id:
            hq = hq.where(Homework.class_id == class_id)
        if subject_id:
            hq = hq.where(Homework.subject_id == subject_id)
        homeworks = (await self.db.execute(hq)).scalars().all()
        # Filter by assigned/due date when present
        kept: list[Homework] = []
        for h in homeworks:
            when = (h.due_at or h.assigned_at)
            if when is not None:
                on = when.date() if hasattr(when, "date") else when
                if on < start or on > end:
                    continue
            kept.append(h)
        if not kept:
            return None
        expected = 0
        for h in kept:
            if student_id:
                if student_id in scope.class_students.get(h.class_id, set()) or (
                    scope.student_class.get(student_id) == h.class_id
                ):
                    expected += 1
            else:
                expected += len(scope.class_students.get(h.class_id, ()))
        if expected <= 0:
            return None
        hw_ids = [h.id for h in kept]
        sq = select(HomeworkSubmission).where(HomeworkSubmission.homework_id.in_(hw_ids))
        if student_id:
            sq = sq.where(HomeworkSubmission.student_id == student_id)
        elif scope.student_ids:
            sq = sq.where(HomeworkSubmission.student_id.in_(scope.student_ids))
        submitted = sum(
            1
            for s in (await self.db.execute(sq)).scalars().all()
            if s.status in GRADED_HW and s.submitted_at is not None
        )
        return optional_rate(submitted, expected)

    def _kpi(self, current: float | None, previous: float | None, unit: str = "percent", **extra) -> KpiValue:
        return KpiValue(value=current, previous=previous, delta=kpi_delta(current, previous), unit=unit, **extra)

    async def dashboard(self, school_id: UUID, f: AnalyticsFilters) -> InsightsDashboardOut:
        start, end, prev_start, prev_end = await self.resolve_window(school_id, f)
        scope = await self._load_scope(school_id, f, start, end)
        subject_id = f.subject_id

        att_rows = await self._attendance_rows(school_id, scope, prev_start, end)
        cur_att, marked, expected, unmarked = self._summarize_attendance(
            att_rows, self._expected_for(scope), start=start, end=end
        )
        prev_days = working_dates(prev_start, prev_end, scope.weekdays, today=prev_end)
        prev_att, _, _, _ = self._summarize_attendance(
            att_rows,
            sum(len(sids) * len(prev_days) for sids in scope.class_students.values()),
            start=prev_start,
            end=prev_end,
        )

        events = await self._score_events(school_id, scope, prev_start, end, subject_id=subject_id)
        cur_events = [e for e in events if start <= e.on <= end]
        prev_events = [e for e in events if prev_start <= e.on <= prev_end]
        avg = self._avg(cur_events)
        prev_avg = self._avg(prev_events)
        pass_rate = self._pass_rate(cur_events)
        prev_pass = self._pass_rate(prev_events)

        fee_rate, _, _ = await self._fee_rates(school_id, scope, start, end)
        prev_fee, _, _ = await self._fee_rates(school_id, scope, prev_start, prev_end)

        teachers = (
            await self.db.execute(
                select(func.count())
                .select_from(Teacher)
                .where(
                    Teacher.school_id == school_id,
                    Teacher.status == PersonStatus.ACTIVE.value,
                )
            )
        ).scalar_one()
        students_n = len(scope.student_ids)
        teachers_n = int(teachers or 0)

        # Weekly trends
        weeks: list[date] = []
        cursor = monday_of(start)
        last = monday_of(end)
        while cursor <= last:
            weeks.append(cursor)
            cursor += timedelta(days=7)
        trends: list[TrendWeek] = []
        for ws in weeks:
            we = min(ws + timedelta(days=6), end)
            if we < start:
                continue
            w_days = working_dates(max(ws, start), we, scope.weekdays)
            w_exp = sum(len(sids) * len(w_days) for sids in scope.class_students.values())
            w_att, _, _, _ = self._summarize_attendance(att_rows, w_exp, start=max(ws, start), end=we)
            w_scores = [e for e in cur_events if max(ws, start) <= e.on <= we]
            w_fee, w_collected, w_assessed = await self._fee_rates(
                school_id, scope, max(ws, start), we
            )
            iso_year, week, _ = max(ws, start).isocalendar()
            trends.append(
                TrendWeek(
                    label=f"W{week:02d}",
                    week_start=ws,
                    attendance_rate=w_att,
                    average_score=self._avg(w_scores),
                    fee_collection_rate=w_fee,
                    fees_collected=w_collected if w_assessed else None,
                )
            )

        # Subject comparison
        by_subj: dict[UUID, list[ScoreEvent]] = defaultdict(list)
        for e in cur_events:
            if e.subject_id:
                by_subj[e.subject_id].append(e)
        subjects = [
            MetricPoint(
                label=scope.subjects[sid].name if sid in scope.subjects else "Subject",
                value=self._avg(evts) or 0,
                meta={"average_score": self._avg(evts), "assessments": len(evts), "subject_id": str(sid)},
            )
            for sid, evts in sorted(by_subj.items(), key=lambda kv: (scope.subjects.get(kv[0]).name if kv[0] in scope.subjects else ""))
        ]
        # Drop zero placeholders — use only real averages in the value if present
        subjects = [
            MetricPoint(label=p.label, value=p.meta["average_score"], meta=p.meta)  # type: ignore[index]
            for p in subjects
            if p.meta and p.meta.get("average_score") is not None
        ]

        buckets = {label: 0 for label, _, _ in GRADE_BUCKETS}
        for e in cur_events:
            for label, lo, hi in GRADE_BUCKETS:
                if lo <= e.percent < hi:
                    buckets[label] += 1
                    break
        grade_distribution = [
            MetricPoint(label=label, value=float(count))
            for label, count in buckets.items()
        ] if cur_events else []

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        attendance_by_day: list[MetricPoint] = []
        for wd in sorted(scope.weekdays):
            days = [d for d in scope.days if d.weekday() == wd]
            exp = sum(len(sids) * len(days) for sids in scope.class_students.values())
            rate, marked_d, expected_d, unmarked_d = self._summarize_attendance(
                att_rows, exp, start=start, end=end, weekdays={wd}
            )
            attendance_by_day.append(
                MetricPoint(
                    label=day_names[wd],
                    value=rate if rate is not None else 0,
                    meta={"attendance_rate": rate, "marked": marked_d, "expected": expected_d, "unmarked": unmarked_d},
                )
            )

        ranks: list[ClassRank] = []
        for room in scope.rooms:
            exp = self._expected_for(scope, room.id)
            rate, _, _, _ = self._summarize_attendance(att_rows, exp, start=start, end=end, class_id=room.id)
            ev = [e for e in cur_events if e.class_id == room.id]
            ranks.append(
                ClassRank(
                    section_id=room.id,
                    class_name=scope.class_names[room.id],
                    students=len(scope.class_students.get(room.id, ())),
                    attendance_rate=rate,
                    average_score=self._avg(ev),
                    pass_rate=self._pass_rate(ev),
                )
            )
        scored = [r for r in ranks if r.average_score is not None or r.attendance_rate is not None]
        def _rank_key(r: ClassRank) -> float:
            if r.average_score is not None:
                return r.average_score
            return r.attendance_rate or -1
        scored_sorted = sorted(scored, key=_rank_key, reverse=True)

        currency = "INR"
        if True:
            cur_row = (
                await self.db.execute(
                    select(FeeInvoice.currency).where(FeeInvoice.school_id == school_id).limit(1)
                )
            ).first()
            if cur_row and cur_row[0]:
                currency = cur_row[0]

        return InsightsDashboardOut(
            from_date=start,
            to_date=end,
            previous_from=prev_start,
            previous_to=prev_end,
            attendance=self._kpi(cur_att, prev_att, marked=marked, expected=expected, unmarked=unmarked),
            average_score=self._kpi(avg, prev_avg, marked=len(cur_events), expected=None, unmarked=None),
            pass_rate=self._kpi(pass_rate, prev_pass, marked=len([e for e in cur_events if e.passed is not None])),
            fee_collection=self._kpi(fee_rate, prev_fee),
            active_students=self._kpi(float(students_n), None, unit="count"),
            teachers=self._kpi(float(teachers_n), None, unit="count"),
            currency=currency,
            trends=trends,
            subjects=subjects,
            grade_distribution=grade_distribution,
            attendance_by_day=attendance_by_day,
            top_classes=scored_sorted[:5],
            bottom_classes=list(reversed(scored_sorted[-5:])) if len(scored_sorted) > 1 else [],
        )

    def _risk_for(self, overall: float | None, subject_avgs: list[float], attendance: float | None) -> list[str]:
        reasons: list[str] = []
        focus_count = sum(1 for v in subject_avgs if v < FOCUS_THRESHOLD)
        if overall is not None and overall < FOCUS_THRESHOLD:
            reasons.append(f"Overall average below {int(FOCUS_THRESHOLD)}%")
        if focus_count >= 2:
            reasons.append(f"{focus_count} subjects below {int(FOCUS_THRESHOLD)}%")
        if attendance is not None and attendance < ATTENDANCE_RISK_THRESHOLD:
            reasons.append(f"Attendance below {int(ATTENDANCE_RISK_THRESHOLD)}%")
        return reasons

    async def progress(self, school_id: UUID, f: AnalyticsFilters) -> InsightsProgressOut:
        start, end, _, _ = await self.resolve_window(school_id, f)
        scope = await self._load_scope(school_id, f, start, end)
        att_rows = await self._attendance_rows(school_id, scope, start, end)
        events = await self._score_events(school_id, scope, start, end, subject_id=f.subject_id)

        # Weekly score trend per student
        weeks: list[date] = []
        cursor = monday_of(start)
        last = monday_of(end)
        while cursor <= last:
            weeks.append(cursor)
            cursor += timedelta(days=7)

        students: list[StudentProgressRow] = []
        for sid in sorted(scope.student_ids, key=lambda x: scope.student_names.get(x, "")):
            cid = scope.student_class.get(sid)
            exp = len(scope.days)  # one slot per working day per student
            rate, _, _, _ = self._summarize_attendance(
                att_rows, exp, start=start, end=end, student_id=sid, class_id=cid
            )
            ev = [e for e in events if e.student_id == sid]
            by_subj: dict[UUID, list[float]] = defaultdict(list)
            for e in ev:
                if e.subject_id:
                    by_subj[e.subject_id].append(e.percent)
            subj_avgs = [sum(vs) / len(vs) for vs in by_subj.values()]
            overall = self._avg(ev)
            hw = await self._homework_rate(school_id, scope, start, end, student_id=sid)
            reasons = self._risk_for(overall, subj_avgs, rate)
            trend: list[MetricPoint] = []
            for ws in weeks:
                we = min(ws + timedelta(days=6), end)
                week_ev = [e for e in ev if max(ws, start) <= e.on <= we]
                avg = self._avg(week_ev)
                _, week, _ = max(ws, start).isocalendar()
                trend.append(MetricPoint(label=f"W{week:02d}", value=avg if avg is not None else 0, meta={"average_score": avg}))
            students.append(
                StudentProgressRow(
                    student_id=sid,
                    student_name=scope.student_names.get(sid, "Student"),
                    section_id=cid,
                    class_name=scope.class_names.get(cid) if cid else None,
                    attendance_rate=rate,
                    average_score=overall,
                    homework_completion=hw,
                    subjects_below_focus=sum(1 for v in subj_avgs if v < FOCUS_THRESHOLD),
                    risk_reasons=reasons,
                    at_risk=bool(reasons),
                    trend=trend,
                )
            )

        # Intelligence-style sort: lowest overall first among at-risk, cap 25
        at_risk = [s for s in students if s.at_risk]
        at_risk.sort(key=lambda s: (s.average_score is not None, s.average_score if s.average_score is not None else 0))
        at_risk = at_risk[:25]

        classes: list[ClassProgressRow] = []
        for room in scope.rooms:
            members = [s for s in students if s.section_id == room.id]
            exp = self._expected_for(scope, room.id)
            rate, _, _, _ = self._summarize_attendance(att_rows, exp, start=start, end=end, class_id=room.id)
            ev = [e for e in events if e.class_id == room.id]
            trend: list[MetricPoint] = []
            for ws in weeks:
                we = min(ws + timedelta(days=6), end)
                week_ev = [e for e in ev if max(ws, start) <= e.on <= we]
                avg = self._avg(week_ev)
                _, week, _ = max(ws, start).isocalendar()
                trend.append(MetricPoint(label=f"W{week:02d}", value=avg if avg is not None else 0, meta={"average_score": avg}))
            classes.append(
                ClassProgressRow(
                    section_id=room.id,
                    class_name=scope.class_names[room.id],
                    students=len(members),
                    attendance_rate=rate,
                    average_score=self._avg(ev),
                    at_risk_count=sum(1 for s in members if s.at_risk),
                    trend=trend,
                )
            )

        return InsightsProgressOut(from_date=start, to_date=end, classes=classes, students=students, at_risk=at_risk)

    async def student(self, school_id: UUID, student_id: UUID, f: AnalyticsFilters) -> StudentInsightOut:
        start, end, _, _ = await self.resolve_window(school_id, f)
        # Ensure the student is visible even if section filter would hide them
        f2 = f.model_copy(update={"section_id": f.section_id})
        scope = await self._load_scope(school_id, f2, start, end)
        if student_id not in scope.student_ids:
            # load just this student
            st = await self.db.get(Student, student_id)
            if not st or st.school_id != school_id:
                raise NotFoundError("Student not found")
            scope.student_ids.add(student_id)
            enr = next((e for e in scope.enrollments if e.student_id == student_id), None)
            if enr is None:
                enr = (
                    await self.db.execute(
                        select(Enrollment).where(
                            Enrollment.school_id == school_id,
                            Enrollment.student_id == student_id,
                            Enrollment.status == EnrollmentStatus.ACTIVE.value,
                        )
                    )
                ).scalar_one_or_none()
            if enr:
                scope.student_class[student_id] = enr.class_id
                scope.class_students[enr.class_id].add(student_id)
            user = await self.db.get(User, st.user_id)
            scope.student_names[student_id] = user.full_name if user else "Student"

        cid = scope.student_class.get(student_id)
        att_rows = await self._attendance_rows(school_id, scope, start, end, student_id=student_id)
        rate, marked, expected, unmarked = self._summarize_attendance(
            att_rows, len(scope.days), start=start, end=end, student_id=student_id, class_id=cid
        )
        events = await self._score_events(school_id, scope, start, end, student_id=student_id, subject_id=f.subject_id)
        by_subj: dict[UUID | None, list[ScoreEvent]] = defaultdict(list)
        for e in events:
            by_subj[e.subject_id].append(e)
        subjects: list[SubjectScore] = []
        subj_avgs: list[float] = []
        for subj_id, evts in by_subj.items():
            avg = self._avg(evts)
            if avg is not None and subj_id is not None:
                subj_avgs.append(avg)
            name = scope.subjects[subj_id].name if subj_id and subj_id in scope.subjects else "Subject"
            subjects.append(
                SubjectScore(
                    subject_id=subj_id,
                    subject_name=name,
                    average_score=avg,
                    assessments=len(evts),
                    below_focus=bool(avg is not None and avg < FOCUS_THRESHOLD),
                )
            )
        subjects.sort(key=lambda s: s.subject_name)
        overall = self._avg(events)
        hw = await self._homework_rate(school_id, scope, start, end, student_id=student_id)
        fee, _, _ = await self._fee_rates(school_id, scope, start, end, student_id=student_id)
        reasons = self._risk_for(overall, subj_avgs, rate)

        weeks: list[date] = []
        cursor = monday_of(start)
        last = monday_of(end)
        while cursor <= last:
            weeks.append(cursor)
            cursor += timedelta(days=7)
        att_trend: list[MetricPoint] = []
        score_trend: list[MetricPoint] = []
        for ws in weeks:
            we = min(ws + timedelta(days=6), end)
            w_days = working_dates(max(ws, start), we, scope.weekdays)
            w_rate, _, _, _ = self._summarize_attendance(
                att_rows, len(w_days), start=max(ws, start), end=we, student_id=student_id, class_id=cid
            )
            week_ev = [e for e in events if max(ws, start) <= e.on <= we]
            _, week, _ = max(ws, start).isocalendar()
            label = f"W{week:02d}"
            att_trend.append(MetricPoint(label=label, value=w_rate if w_rate is not None else 0, meta={"attendance_rate": w_rate}))
            avg = self._avg(week_ev)
            score_trend.append(MetricPoint(label=label, value=avg if avg is not None else 0, meta={"average_score": avg}))

        return StudentInsightOut(
            student_id=student_id,
            student_name=scope.student_names.get(student_id, "Student"),
            section_id=cid,
            class_name=scope.class_names.get(cid) if cid else None,
            from_date=start,
            to_date=end,
            attendance_rate=rate,
            average_score=overall,
            pass_rate=self._pass_rate(events),
            homework_completion=hw,
            fee_collection_rate=fee,
            unmarked_days=unmarked,
            expected_days=expected,
            marked_days=marked,
            at_risk=bool(reasons),
            risk_reasons=reasons,
            subjects=subjects,
            attendance_trend=att_trend,
            score_trend=score_trend,
        )

    async def heatmap(self, school_id: UUID, f: AnalyticsFilters, mode: str = "class_subject") -> InsightsHeatmapOut:
        start, end, _, _ = await self.resolve_window(school_id, f)
        scope = await self._load_scope(school_id, f, start, end)
        att_rows = await self._attendance_rows(school_id, scope, start, end)
        events = await self._score_events(school_id, scope, start, end, subject_id=f.subject_id)
        mode = mode if mode in {"class_subject", "class_week"} else "class_subject"

        rows = [HeatmapAxis(id=str(r.id), label=scope.class_names[r.id]) for r in scope.rooms]
        cells: list[HeatmapCell] = []

        if mode == "class_week":
            weeks: list[date] = []
            cursor = monday_of(start)
            last = monday_of(end)
            while cursor <= last:
                weeks.append(cursor)
                cursor += timedelta(days=7)
            columns = [HeatmapAxis(id=iso_week_label(ws), label=iso_week_label(ws)) for ws in weeks]
            for room in scope.rooms:
                for ws in weeks:
                    we = min(ws + timedelta(days=6), end)
                    w_days = working_dates(max(ws, start), we, scope.weekdays)
                    exp = len(scope.class_students.get(room.id, ())) * len(w_days)
                    rate, marked, expected, unmarked = self._summarize_attendance(
                        att_rows, exp, start=max(ws, start), end=we, class_id=room.id
                    )
                    ev = [e for e in events if e.class_id == room.id and max(ws, start) <= e.on <= we]
                    avg = self._avg(ev)
                    cells.append(
                        HeatmapCell(
                            row_id=str(room.id),
                            column_id=iso_week_label(ws),
                            attendance_rate=rate,
                            average_score=avg,
                            marked=marked,
                            expected=expected,
                            unmarked=unmarked,
                            assessments=len(ev),
                            attendance_band=score_band(rate),
                            score_band=score_band(avg),
                        )
                    )
        else:
            subject_ids: list[UUID] = []
            seen: set[UUID] = set()
            for room in scope.rooms:
                for sid in sorted(scope.class_subjects.get(room.id, ()), key=lambda x: scope.subjects[x].name if x in scope.subjects else ""):
                    if f.subject_id and sid != f.subject_id:
                        continue
                    if sid not in seen:
                        seen.add(sid)
                        subject_ids.append(sid)
            if not subject_ids:
                subject_ids = list(scope.subjects)
            columns = [
                HeatmapAxis(id=str(sid), label=scope.subjects[sid].name if sid in scope.subjects else "Subject")
                for sid in subject_ids
            ]
            for room in scope.rooms:
                exp = self._expected_for(scope, room.id)
                # Attendance is class-level (not per subject) — same rate across subject columns,
                # but scores are per subject. Both are shown on hover.
                rate, marked, expected, unmarked = self._summarize_attendance(
                    att_rows, exp, start=start, end=end, class_id=room.id
                )
                for sid in subject_ids:
                    ev = [e for e in events if e.class_id == room.id and e.subject_id == sid]
                    avg = self._avg(ev)
                    cells.append(
                        HeatmapCell(
                            row_id=str(room.id),
                            column_id=str(sid),
                            attendance_rate=rate,
                            average_score=avg,
                            marked=marked,
                            expected=expected,
                            unmarked=unmarked,
                            assessments=len(ev),
                            attendance_band=score_band(rate),
                            score_band=score_band(avg),
                        )
                    )

        return InsightsHeatmapOut(
            mode=mode,
            from_date=start,
            to_date=end,
            rows=rows,
            columns=columns,
            cells=cells,
            legend=HEATMAP_LEGEND,
        )

    async def heatmap_cell(
        self,
        school_id: UUID,
        f: AnalyticsFilters,
        *,
        section_id: UUID,
        mode: str = "class_subject",
        column_id: str,
    ) -> InsightsHeatmapDrillOut:
        start, end, _, _ = await self.resolve_window(school_id, f)
        f2 = f.model_copy(update={"section_id": section_id})
        scope = await self._load_scope(school_id, f2, start, end)
        room = next((r for r in scope.rooms if r.id == section_id), None)
        if room is None:
            raise NotFoundError("Class not found")
        att_rows = await self._attendance_rows(school_id, scope, start, end, class_id=section_id)
        events = await self._score_events(school_id, scope, start, end, subject_id=f.subject_id)
        mode = mode if mode in {"class_subject", "class_week"} else "class_subject"

        col_label = column_id
        cell_start, cell_end = start, end
        subject_id = None
        if mode == "class_week":
            # column_id like 2026-W44
            try:
                year_s, week_s = column_id.split("-W")
                cell_start = date.fromisocalendar(int(year_s), int(week_s), 1)
                cell_end = min(cell_start + timedelta(days=6), end)
                cell_start = max(cell_start, start)
                col_label = column_id
            except ValueError:
                cell_start, cell_end = start, end
        else:
            try:
                subject_id = UUID(column_id)
            except ValueError as exc:
                raise NotFoundError("Subject not found") from exc
            subj = scope.subjects.get(subject_id)
            col_label = subj.name if subj else column_id

        students: list[HeatmapDrillStudent] = []
        for sid in sorted(scope.class_students.get(section_id, ()), key=lambda x: scope.student_names.get(x, "")):
            days = working_dates(cell_start, cell_end, scope.weekdays)
            rate, marked, expected, unmarked = self._summarize_attendance(
                att_rows, len(days), start=cell_start, end=cell_end, student_id=sid, class_id=section_id
            )
            ev = [
                e
                for e in events
                if e.student_id == sid
                and cell_start <= e.on <= cell_end
                and (subject_id is None or e.subject_id == subject_id)
            ]
            students.append(
                HeatmapDrillStudent(
                    student_id=sid,
                    student_name=scope.student_names.get(sid, "Student"),
                    attendance_rate=rate,
                    average_score=self._avg(ev),
                    marked=marked,
                    expected=expected,
                    unmarked=unmarked,
                    assessments=len(ev),
                )
            )
        ev_all = [
            e
            for e in events
            if e.class_id == section_id
            and cell_start <= e.on <= cell_end
            and (subject_id is None or e.subject_id == subject_id)
        ]
        days = working_dates(cell_start, cell_end, scope.weekdays)
        class_rate, _, _, _ = self._summarize_attendance(
            att_rows,
            len(scope.class_students.get(section_id, ())) * len(days),
            start=cell_start,
            end=cell_end,
            class_id=section_id,
        )
        return InsightsHeatmapDrillOut(
            mode=mode,
            class_name=scope.class_names[section_id],
            column_label=col_label,
            attendance_rate=class_rate,
            average_score=self._avg(ev_all),
            students=students,
        )

    async def report_history(self, school_id: UUID, limit: int = 50) -> list[ReportHistoryItem]:
        rows = (
            await self.db.execute(
                select(ReportAuditLog)
                .where(ReportAuditLog.school_id == school_id)
                .order_by(ReportAuditLog.created_at.desc())
                .limit(min(max(limit, 1), 200))
            )
        ).scalars().all()
        out: list[ReportHistoryItem] = []
        names = {c["id"]: c["name"] for c in REPORT_CATALOG}
        for row in rows:
            filters = None
            if row.filters_json:
                try:
                    filters = json.loads(row.filters_json)
                except json.JSONDecodeError:
                    filters = None
            out.append(
                ReportHistoryItem(
                    id=row.id,
                    report_type=row.report_type,
                    action=row.action,
                    filters=filters,
                    created_at=row.created_at,
                    entity_id=row.entity_id,
                )
            )
        # names unused except to keep catalog referenced for future display
        _ = names
        return out
