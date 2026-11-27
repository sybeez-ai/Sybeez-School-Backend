"""Unified school-admin exam hub: written exams and online tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.db.models import ClassRoom, Exam, ExamSchedule, ExamSubject, OnlineTest, OnlineTestQuestion, Subject
from src.modules.exams.schemas import (
    ClassTestCreate,
    ExamHubItemOut,
    ExamHubListOut,
    ExamHubRescheduleIn,
    ExamHubScheduleIn,
    ExamHubScheduleOut,
)
from src.modules.exams.service import ExamService, _utcnow
from src.modules.online_tests import schemas as OT
from src.modules.online_tests.service import OnlineTestService
from src.shared.enums import ExamStatus, UserRole
from src.shared.test_workflow import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    WORKFLOW_COMPLETED,
    WORKFLOW_LIVE,
    WORKFLOW_PENDING,
    WORKFLOW_SCHEDULED,
    combine_start,
    ends_from_duration,
    workflow_label,
    workflow_status,
)
from src.shared.utils import format_class_label


def _live_status(wf: str) -> str:
    if wf == WORKFLOW_PENDING:
        return "pending_approval"
    if wf == WORKFLOW_LIVE:
        return "live"
    if wf == WORKFLOW_COMPLETED:
        return "completed"
    if wf == WORKFLOW_SCHEDULED:
        return "scheduled"
    return "draft"


def _countdown(starts_at: datetime | None, ends_at: datetime | None, now: datetime) -> int | None:
    if starts_at and now < starts_at:
        return max(0, int((starts_at - now).total_seconds()))
    if starts_at and ends_at and starts_at <= now < ends_at:
        return max(0, int((ends_at - now).total_seconds()))
    return None


def _tab_for(live_status: str) -> str:
    if live_status == "pending_approval":
        return "pending"
    if live_status == "live":
        return "live"
    if live_status == "completed":
        return "completed"
    return "upcoming"


class ExamHubService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.exams = ExamService(db)

    def _is_admin(self, user) -> bool:
        role = getattr(getattr(user, "role", None), "value", getattr(user, "role", None))
        return role in {UserRole.SCHOOL_ADMIN.value, "SCHOOL_ADMIN", "PLATFORM_ADMIN"}

    async def _class_label(self, class_id: UUID) -> str:
        room = await self.db.get(ClassRoom, class_id)
        if not room:
            return "Class"
        return format_class_label(room.name, room.section)

    async def _subject_name(self, subject_id: UUID | None) -> str | None:
        if not subject_id:
            return None
        row = await self.db.get(Subject, subject_id)
        return row.name if row else None

    async def _written_item(self, exam: Exam, now: datetime) -> ExamHubItemOut:
        out = await self.exams._to_exam_out(exam)
        first = out.schedules[0] if out.schedules else None
        starts = combine_start(first.exam_date if first else exam.starts_on, first.starts_at if first else None)
        duration = out.subjects[0].duration_minutes if out.subjects else None
        ends = ends_from_duration(starts, duration)
        if first and first.ends_at and first.exam_date:
            ends = combine_start(first.exam_date, first.ends_at)
        wf = out.workflow_status
        live = _live_status(wf)
        return ExamHubItemOut(
            id=exam.id,
            kind="written",
            title=exam.title,
            subject_id=out.subjects[0].subject_id if out.subjects else exam.subject_id,
            subject_name=out.subjects[0].subject_name if out.subjects else await self._subject_name(exam.subject_id),
            class_ids=[s.class_id for s in out.sections],
            class_names=[s.class_name or "Class" for s in out.sections],
            exam_date=first.exam_date if first else exam.starts_on,
            starts_at=starts,
            ends_at=ends,
            duration_minutes=duration,
            approval_status=out.approval_status,
            workflow_status=wf,
            workflow_label=out.workflow_label,
            live_status=live,
            countdown_seconds=_countdown(starts, ends, now),
            question_count=0,
            max_marks=out.subjects[0].max_marks if out.subjects else exam.max_score,
        )

    async def _online_item(self, test: OnlineTest, now: datetime, question_count: int | None = None) -> ExamHubItemOut:
        if question_count is None:
            question_count = int(
                (
                    await self.db.execute(
                        select(func.count()).select_from(OnlineTestQuestion).where(OnlineTestQuestion.test_id == test.id)
                    )
                ).scalar_one()
                or 0
            )
        starts = test.opens_at
        ends = test.closes_at or ends_from_duration(starts, test.duration_minutes)
        # Online "PUBLISHED" means the sitting is issued, not that results are out.
        # If a window exists, the clock decides live vs scheduled vs completed.
        # If there is no window, treat it as a historical completed test.
        record = test.status
        if (record or "").upper() == "PUBLISHED":
            record = "SCHEDULED" if (starts or ends) else "CLOSED"
        wf = workflow_status(
            approval_status=test.approval_status,
            starts_at=starts,
            ends_at=ends,
            record_status=record,
            now=now,
        )
        live = _live_status(wf)
        return ExamHubItemOut(
            id=test.id,
            kind="online",
            title=test.title,
            subject_id=test.subject_id,
            subject_name=await self._subject_name(test.subject_id),
            class_ids=[test.class_id],
            class_names=[await self._class_label(test.class_id)],
            exam_date=(starts or test.published_at).date() if (starts or test.published_at) else None,
            starts_at=starts,
            ends_at=ends,
            duration_minutes=test.duration_minutes,
            approval_status=test.approval_status or "DRAFT",
            workflow_status=wf,
            workflow_label=workflow_label(wf),
            live_status=live,
            countdown_seconds=_countdown(starts, ends, now),
            question_count=question_count,
            max_marks=float(test.total_points or 0) or None,
        )

    async def list_hub(self, school_id: UUID, *, tab: str | None = None) -> ExamHubListOut:
        now = _utcnow()
        exams = (
            await self.db.execute(
                select(Exam).where(Exam.school_id == school_id).order_by(Exam.starts_on.desc().nullslast(), Exam.created_at.desc())
            )
        ).scalars().all()
        tests = (
            await self.db.execute(
                select(OnlineTest).where(OnlineTest.school_id == school_id).order_by(OnlineTest.opens_at.desc().nullslast(), OnlineTest.created_at.desc())
            )
        ).scalars().all()
        items: list[ExamHubItemOut] = []
        for exam in exams:
            items.append(await self._written_item(exam, now))
        for test in tests:
            items.append(await self._online_item(test, now))
        counts = {"upcoming": 0, "live": 0, "completed": 0, "pending": 0}
        for item in items:
            counts[_tab_for(item.live_status)] = counts.get(_tab_for(item.live_status), 0) + 1
        if tab and tab != "all":
            items = [i for i in items if _tab_for(i.live_status) == tab]
        def sort_key(item: ExamHubItemOut):
            when = item.starts_at or datetime.min.replace(tzinfo=timezone.utc)
            return (0 if item.live_status == "live" else 1, when)
        items.sort(key=sort_key)
        return ExamHubListOut(items=items, counts=counts)

    async def schedule(self, school_id: UUID, user, data: ExamHubScheduleIn) -> ExamHubScheduleOut:
        if not data.class_ids:
            raise AppError("Select at least one class", status_code=422)
        start_now = data.action == "start_now"
        schedule_now = data.action == "schedule" and self._is_admin(user)
        submit = data.action == "schedule" and not self._is_admin(user)
        exam_date = data.exam_date
        start_time = data.start_time
        if start_now:
            now = _utcnow()
            exam_date = now.date()
            start_time = now.time().replace(tzinfo=None, microsecond=0)

        if data.kind == "written":
            exam = await self.exams.create_class_test(
                school_id,
                user,
                ClassTestCreate(
                    title=data.title,
                    class_ids=data.class_ids,
                    subject_id=data.subject_id,
                    exam_date=exam_date,
                    start_time=start_time,
                    duration_minutes=data.duration_minutes,
                    description=data.description,
                    max_marks=data.max_marks,
                    question_ids=data.question_ids,
                    submit_for_approval=submit,
                    schedule_now=schedule_now and not start_now,
                    start_now=start_now,
                ),
            )
            item = await self._written_item(await self.exams._get_exam(school_id, exam.id), _utcnow())
            return ExamHubScheduleOut(kind="written", items=[item])

        if data.kind == "online":
            if data.duration_minutes > 300:
                raise AppError("Online tests can last at most 300 minutes", status_code=422)
            if data.action in {"schedule", "start_now"} and not data.question_ids and not data.new_questions:
                raise AppError("Add questions from the bank or create new ones before scheduling an online test", status_code=422)

        ot = OnlineTestService(self.db)
        items: list[ExamHubItemOut] = []
        opens = combine_start(exam_date, start_time)
        closes = ends_from_duration(opens, data.duration_minutes)
        for class_id in data.class_ids:
            created = await ot.create_test(
                user,
                OT.OnlineTestCreate(
                    class_id=class_id,
                    subject_id=data.subject_id,
                    title=data.title,
                    instructions=data.description,
                    duration_minutes=data.duration_minutes,
                    opens_at=opens,
                    closes_at=closes,
                ),
            )
            test_id = created.id
            if data.question_ids:
                await ot.add_questions_from_bank(user, test_id, data.question_ids)
            if data.new_questions:
                bulk = []
                for q in data.new_questions:
                    if len(q.options) < 2:
                        continue
                    idx = min(max(q.correct_index, 0), len(q.options) - 1)
                    bulk.append(
                        OT.QuestionBulkItem(
                            prompt=q.prompt,
                            options=q.options,
                            correct_index=idx,
                            points=q.points or 1,
                            source="MANUAL",
                            reviewed=True,
                        )
                    )
                if bulk:
                    await ot.add_questions(user, test_id, bulk, None)
            test = await self.db.get(OnlineTest, test_id)
            if test is None:
                raise NotFoundError("Online test not found")
            if data.action == "schedule" and self._is_admin(user):
                await ot.approve_test(user, test_id)
            elif data.action == "start_now":
                if not self._is_admin(user):
                    raise ForbiddenError("Only a school admin can start a test now")
                now = _utcnow()
                test.opens_at = now
                test.closes_at = now + timedelta(minutes=data.duration_minutes)
                await self.db.flush()
                await ot.approve_test(user, test_id)
            elif submit:
                await ot.submit_for_approval(user, test_id)
            items.append(await self._online_item(await self.db.get(OnlineTest, test_id), _utcnow()))  # type: ignore[arg-type]
        return ExamHubScheduleOut(kind="online", items=items)

    async def start_now(self, school_id: UUID, user, *, kind: str, item_id: UUID) -> ExamHubItemOut:
        if not self._is_admin(user):
            raise ForbiddenError("Only a school admin can start an exam now")
        now = _utcnow()
        if kind == "written":
            exam = await self.exams._get_exam(school_id, item_id)
            if exam.approval_status == APPROVAL_PENDING:
                await self.exams.approve_exam(school_id, user.id, item_id)
                exam = await self.exams._get_exam(school_id, item_id)
            exam.approval_status = APPROVAL_APPROVED
            exam.approved_at = exam.approved_at or now
            exam.approved_by = exam.approved_by or user.id
            exam.status = ExamStatus.ONGOING.value
            exam.starts_on = now.date()
            exam.scheduled_at = now
            start_clock = now.time().replace(tzinfo=None, microsecond=0)
            schedules = (
                await self.db.execute(select(ExamSchedule).where(ExamSchedule.exam_id == exam.id))
            ).scalars().all()
            subjects = (
                await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam.id))
            ).scalars().all()
            duration = subjects[0].duration_minutes if subjects and subjects[0].duration_minutes else 45
            end_clock = (datetime.combine(now.date(), start_clock) + timedelta(minutes=duration)).time()
            for sch in schedules:
                sch.exam_date = now.date()
                sch.starts_at = start_clock
                sch.ends_at = end_clock
            for subj in subjects:
                subj.exam_date = now.date()
                subj.starts_at = start_clock
                subj.ends_at = end_clock
                if not subj.duration_minutes:
                    subj.duration_minutes = duration
            exam.ends_on = now.date()
            await self.db.commit()
            return await self._written_item(await self.exams._get_exam(school_id, item_id), _utcnow())

        if kind != "online":
            raise AppError("Unknown exam type", status_code=422)
        test = await self.db.get(OnlineTest, item_id)
        if not test or test.school_id != school_id:
            raise NotFoundError("Online test not found")
        ot = OnlineTestService(self.db)
        duration = test.duration_minutes or 30
        test.opens_at = now
        test.closes_at = now + timedelta(minutes=duration)
        await self.db.flush()
        if (test.approval_status or "DRAFT") != APPROVAL_APPROVED or test.status == "DRAFT":
            await ot.approve_test(user, item_id)
        else:
            await self.db.commit()
        test = await self.db.get(OnlineTest, item_id)
        return await self._online_item(test, _utcnow())  # type: ignore[arg-type]

    async def reschedule(self, school_id: UUID, user, *, kind: str, item_id: UUID, data: ExamHubRescheduleIn) -> ExamHubItemOut:
        if kind == "written":
            exam = await self.exams._get_exam(school_id, item_id)
            if not self._is_admin(user) and exam.created_by != user.id:
                raise ForbiddenError("You cannot reschedule this exam")
            ends = (datetime.combine(data.exam_date, data.start_time) + timedelta(minutes=data.duration_minutes)).time()
            exam.starts_on = data.exam_date
            exam.ends_on = data.exam_date
            exam.scheduled_at = combine_start(data.exam_date, data.start_time)
            if exam.status == ExamStatus.ONGOING.value:
                exam.status = ExamStatus.SCHEDULED.value
            schedules = (
                await self.db.execute(select(ExamSchedule).where(ExamSchedule.exam_id == exam.id))
            ).scalars().all()
            subjects = (
                await self.db.execute(select(ExamSubject).where(ExamSubject.exam_id == exam.id))
            ).scalars().all()
            for sch in schedules:
                sch.exam_date = data.exam_date
                sch.starts_at = data.start_time
                sch.ends_at = ends
            for subj in subjects:
                subj.exam_date = data.exam_date
                subj.starts_at = data.start_time
                subj.ends_at = ends
                subj.duration_minutes = data.duration_minutes
            await self.db.commit()
            return await self._written_item(await self.exams._get_exam(school_id, item_id), _utcnow())

        if kind != "online":
            raise AppError("Unknown exam type", status_code=422)
        test = await self.db.get(OnlineTest, item_id)
        if not test or test.school_id != school_id:
            raise NotFoundError("Online test not found")
        if not self._is_admin(user):
            raise ForbiddenError("Only a school admin can reschedule this test")
        opens = combine_start(data.exam_date, data.start_time)
        test.opens_at = opens
        test.closes_at = ends_from_duration(opens, data.duration_minutes)
        test.duration_minutes = data.duration_minutes
        if test.status == "CLOSED":
            test.status = "PUBLISHED"
        await self.db.commit()
        return await self._online_item(test, _utcnow())
