"""Online MCQ tests: authoring, secure attempts, server-side grading, review and results.

Security model
* The answer key lives in ``online_test_question_keys`` which RLS limits to staff and the class's
  teachers. Student-facing code paths never load it; grading runs inside ``service_scope`` (RLS bypass for
  one narrow block, the same mechanism notifications use) and only writes score fields.
* Time is enforced with the database clock (``clock_timestamp()``). An attempt past its deadline is
  finalised lazily on any request and by the worker sweeper, so a closed laptop cannot extend a test.
* Students write only their own answers and events, and only while the attempt is open (RLS too).
"""

from __future__ import annotations

import csv
import io
import random
import secrets
import statistics
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    ClassRoom,
    Enrollment,
    GradeRule,
    GradingScale,
    OnlineTest,
    OnlineTestAiUsage,
    OnlineTestAnswer,
    OnlineTestAttempt,
    OnlineTestAuditLog,
    OnlineTestEvent,
    OnlineTestQuestion,
    OnlineTestQuestionKey,
    OnlineTestSchoolSettings,
    Parent,
    Student,
    StudentParent,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.fees.tenant import bind_tenant, set_rls_bypass
from src.modules.notifications.service import create_in_app_notification, notification_fanout_scope
from src.modules.online_tests import schemas as S
from src.modules.online_tests.push import send_push
from src.shared.enums import EnrollmentStatus, UserRole

service_scope = notification_fanout_scope  # narrow, transaction-local RLS bypass

IN_PROGRESS = "IN_PROGRESS"
SUBMITTED = "SUBMITTED"
AUTO_SUBMITTED = "AUTO_SUBMITTED"
VOID = "VOID"
FINAL_STATES = (SUBMITTED, AUTO_SUBMITTED)
GRACE_SECONDS = 5  # network slack for the last answer / submit before the deadline closes the door
COUNTED_EVENTS = {
    "FULLSCREEN_EXIT", "TAB_HIDDEN", "WINDOW_BLUR", "COPY_ATTEMPT", "PASTE_ATTEMPT", "PRINT_ATTEMPT",
    # native app: leaving the app, capturing the screen, split-screen, or breaking the OS lockdown. LOCKDOWN_UNAVAILABLE
    # (device cannot pin the screen) is logged for the teacher but never counts against the student.
    "APP_BACKGROUND", "SCREENSHOT_ATTEMPT", "SCREEN_RECORDING", "MULTI_WINDOW", "LOCKDOWN_BROKEN",
}
DEDUPE_SECONDS = 1.5  # blur + hidden fire together for one tab switch; count it once
_rng = random.SystemRandom()


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class OnlineTestService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── small helpers ────────────────────────────────────────

    async def _now(self) -> datetime:
        return (await self.db.execute(select(func.clock_timestamp()))).scalar_one()

    @staticmethod
    def _sid(user: CurrentUser) -> UUID:
        if user.school_id is None:
            raise ForbiddenError("School context required")
        return user.school_id

    @staticmethod
    def _is_admin(user: CurrentUser) -> bool:
        return user.role in (UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN)

    async def _user_name(self, user_id: UUID | None) -> str | None:
        if not user_id:
            return None
        return (await self.db.execute(select(User.full_name).where(User.id == user_id))).scalar_one_or_none()

    async def _student_name(self, student_id: UUID) -> str | None:
        student = await self.db.get(Student, student_id)
        return await self._user_name(student.user_id) if student else None

    async def _teacher(self, user: CurrentUser) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == self._sid(user), Teacher.user_id == user.id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise ForbiddenError("Teacher profile required")
        return teacher

    async def _student(self, user: CurrentUser) -> Student:
        student = (
            await self.db.execute(
                select(Student).where(Student.school_id == self._sid(user), Student.user_id == user.id)
            )
        ).scalar_one_or_none()
        if not student:
            raise ForbiddenError("Student profile required")
        return student

    async def _audit(self, school_id, actor, action: str, *, test_id=None, attempt_id=None, details=None) -> None:
        async with service_scope(self.db, school_id):
            self.db.add(
                OnlineTestAuditLog(
                    school_id=school_id,
                    test_id=test_id,
                    attempt_id=attempt_id,
                    actor_user_id=actor,
                    action=action,
                    details=details,
                )
            )
            await self.db.flush()

    # ── school settings ──────────────────────────────────────

    async def school_settings(self, school_id: UUID) -> OnlineTestSchoolSettings:
        row = (
            await self.db.execute(
                select(OnlineTestSchoolSettings).where(OnlineTestSchoolSettings.school_id == school_id)
            )
        ).scalar_one_or_none()
        if row:
            return row
        async with service_scope(self.db, school_id):
            row = OnlineTestSchoolSettings(
                school_id=school_id, enabled=True, ai_monthly_cap=200, default_max_violations=3
            )
            self.db.add(row)
            await self.db.flush()
        return row

    async def assert_enabled(self, school_id: UUID) -> OnlineTestSchoolSettings:
        row = await self.school_settings(school_id)
        if not row.enabled:
            raise ForbiddenError("Online tests are switched off for your school.", code="online_tests_disabled")
        return row

    async def _ai_used(self, school_id: UUID) -> int:
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        return int(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(OnlineTestAiUsage.returned), 0)).where(
                        OnlineTestAiUsage.school_id == school_id, OnlineTestAiUsage.period == period
                    )
                )
            ).scalar_one()
        )

    async def get_settings_out(self, user: CurrentUser) -> S.SchoolSettingsOut:
        sid = self._sid(user)
        row = await self.school_settings(sid)
        staff = user.role in (UserRole.TEACHER, UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN)
        return S.SchoolSettingsOut(
            enabled=row.enabled,
            ai_monthly_cap=row.ai_monthly_cap if staff else 0,
            ai_used_this_month=await self._ai_used(sid) if staff else 0,
            default_max_violations=row.default_max_violations,
        )

    async def update_settings(self, user: CurrentUser, body: S.SchoolSettingsIn) -> S.SchoolSettingsOut:
        sid = self._sid(user)
        row = await self.school_settings(sid)
        changes = body.model_dump(exclude_none=True)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_by = user.id
        await self.db.flush()
        await self._audit(sid, user.id, "SETTINGS_UPDATED", details=changes)
        return await self.get_settings_out(user)

    # ── lookups / authorization ─────────────────────────────

    async def _get_test(self, school_id: UUID, test_id: UUID) -> OnlineTest:
        test = await self.db.get(OnlineTest, test_id)
        if not test or test.school_id != school_id:
            raise NotFoundError("Test not found")
        return test

    async def _class_assigned(self, teacher: Teacher, class_id: UUID) -> bool:
        classroom = await self.db.get(ClassRoom, class_id)
        if classroom and classroom.class_teacher_id == teacher.id:
            return True
        return (
            await self.db.execute(
                select(TeacherAssignment.id)
                .where(TeacherAssignment.teacher_id == teacher.id, TeacherAssignment.class_id == class_id)
                .limit(1)
            )
        ).scalar_one_or_none() is not None

    async def _manage_test(self, user: CurrentUser, test_id: UUID, *, write: bool) -> OnlineTest:
        sid = self._sid(user)
        test = await self._get_test(sid, test_id)
        if self._is_admin(user):
            return test
        if user.role != UserRole.TEACHER:
            raise ForbiddenError("Only teachers and school admins can manage tests")
        teacher = await self._teacher(user)
        if test.teacher_id == teacher.id:
            return test
        if not write and test.status != "DRAFT" and await self._class_assigned(teacher, test.class_id):
            return test
        raise ForbiddenError("You can only manage your own tests", code="not_test_owner")

    async def _assert_can_author(self, user: CurrentUser, class_id: UUID, subject_id: UUID) -> Teacher | None:
        sid = self._sid(user)
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != sid:
            raise NotFoundError("Class not found")
        subject = await self.db.get(Subject, subject_id)
        if not subject or subject.school_id != sid:
            raise NotFoundError("Subject not found")
        if self._is_admin(user):
            return None
        teacher = await self._teacher(user)
        assigned = (
            await self.db.execute(
                select(TeacherAssignment.id)
                .where(
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == class_id,
                    TeacherAssignment.subject_id == subject_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if not assigned and classroom.class_teacher_id != teacher.id:
            raise ForbiddenError("You are not assigned to teach this subject in this class")
        return teacher

    async def _has_attempts(self, test_id: UUID) -> bool:
        return (
            await self.db.execute(select(OnlineTestAttempt.id).where(OnlineTestAttempt.test_id == test_id).limit(1))
        ).scalar_one_or_none() is not None

    async def _assert_editable(self, test: OnlineTest) -> None:
        if test.status == "CLOSED":
            raise ConflictError("This test is closed", code="test_closed")
        if test.status == "PUBLISHED" and await self._has_attempts(test.id):
            raise ConflictError(
                "Students have already started this test, so it can no longer be edited", code="test_locked"
            )

    @staticmethod
    def _settings(test: OnlineTest) -> S.OnlineTestSettings:
        return S.OnlineTestSettings(**(test.settings or {}))

    @staticmethod
    def _effective(test: OnlineTest, now: datetime) -> str:
        if test.status in ("DRAFT", "CLOSED"):
            return test.status
        if test.opens_at and now < test.opens_at:
            return "UPCOMING"
        if test.closes_at and now >= test.closes_at:
            return "CLOSED"
        return "LIVE"

    # ── serialisation ────────────────────────────────────────

    async def _counts(self, test_ids: list[UUID]) -> dict[UUID, dict]:
        out = {t: {"q": 0, "total": 0, "sub": 0, "flag": 0} for t in test_ids}
        if not test_ids:
            return out
        school_id = (await self.db.execute(select(OnlineTest.school_id).where(OnlineTest.id == test_ids[0]))).scalar_one()
        async with service_scope(self.db, school_id):
            await self._fill_counts(out, test_ids)
        return out

    async def _fill_counts(self, out: dict, test_ids: list[UUID]) -> None:
        # Aggregates only (no row content), so a student can be shown the question count before starting.
        for tid, n in (
            await self.db.execute(
                select(OnlineTestQuestion.test_id, func.count())
                .where(OnlineTestQuestion.test_id.in_(test_ids))
                .group_by(OnlineTestQuestion.test_id)
            )
        ).all():
            out[tid]["q"] = n
        for tid, status, review, n in (
            await self.db.execute(
                select(
                    OnlineTestAttempt.test_id, OnlineTestAttempt.status, OnlineTestAttempt.review_status, func.count()
                )
                .where(OnlineTestAttempt.test_id.in_(test_ids))
                .group_by(OnlineTestAttempt.test_id, OnlineTestAttempt.status, OnlineTestAttempt.review_status)
            )
        ).all():
            out[tid]["total"] += n
            if status in FINAL_STATES:
                out[tid]["sub"] += n
            if review == "PENDING":
                out[tid]["flag"] += n

    async def _test_out(self, test: OnlineTest, now: datetime, counts: dict | None = None) -> S.OnlineTestOut:
        c = counts or (await self._counts([test.id]))[test.id]
        teacher_name = None
        if test.teacher_id:
            teacher = await self.db.get(Teacher, test.teacher_id)
            teacher_name = await self._user_name(teacher.user_id) if teacher else None
        classroom = await self.db.get(ClassRoom, test.class_id)
        subject = await self.db.get(Subject, test.subject_id)
        return S.OnlineTestOut(
            id=test.id,
            school_id=test.school_id,
            class_id=test.class_id,
            class_name=classroom.name if classroom else None,
            subject_id=test.subject_id,
            subject_name=subject.name if subject else None,
            teacher_id=test.teacher_id,
            teacher_name=teacher_name,
            title=test.title,
            instructions=test.instructions,
            status=test.status,
            effective_status=self._effective(test, now),
            duration_minutes=test.duration_minutes,
            opens_at=test.opens_at,
            closes_at=test.closes_at,
            total_points=test.total_points,
            question_count=c["q"],
            settings=self._settings(test),
            published_at=test.published_at,
            results_released_at=test.results_released_at,
            attempts_total=c["total"],
            attempts_submitted=c["sub"],
            attempts_flagged=c["flag"],
            created_at=test.created_at,
        )

    @staticmethod
    def _q_teacher_out(q: OnlineTestQuestion, key: OnlineTestQuestionKey | None) -> S.TeacherQuestionOut:
        options = [S.OptionOut(**o) for o in q.options]
        idx = next((i for i, o in enumerate(options) if key and o.id == key.correct_option_id), None)
        return S.TeacherQuestionOut(
            id=q.id,
            prompt=q.prompt,
            options=options,
            correct_option_id=key.correct_option_id if key else None,
            correct_index=idx,
            explanation=key.explanation if key else None,
            points=q.points,
            sort_order=q.sort_order,
            difficulty=q.difficulty,
            source=q.source,
            ai_reviewed=q.ai_reviewed,
        )

    async def _questions(self, test_id: UUID) -> list[OnlineTestQuestion]:
        return list(
            (
                await self.db.execute(
                    select(OnlineTestQuestion)
                    .where(OnlineTestQuestion.test_id == test_id)
                    .order_by(OnlineTestQuestion.sort_order, OnlineTestQuestion.created_at)
                )
            ).scalars()
        )

    async def _keys(self, test_id: UUID) -> dict[UUID, OnlineTestQuestionKey]:
        rows = (
            await self.db.execute(select(OnlineTestQuestionKey).where(OnlineTestQuestionKey.test_id == test_id))
        ).scalars()
        return {k.question_id: k for k in rows}

    async def _recount(self, test: OnlineTest) -> None:
        total = (
            await self.db.execute(
                select(func.coalesce(func.sum(OnlineTestQuestion.points), 0)).where(
                    OnlineTestQuestion.test_id == test.id
                )
            )
        ).scalar_one()
        test.total_points = int(total)
        await self.db.flush()

    # ── test CRUD ────────────────────────────────────────────

    async def create_test(self, user: CurrentUser, body: S.OnlineTestCreate) -> S.OnlineTestDetailOut:
        sid = self._sid(user)
        school = await self.assert_enabled(sid)
        teacher = await self._assert_can_author(user, body.class_id, body.subject_id)
        settings = body.settings
        if "settings" not in body.model_fields_set:
            settings = S.OnlineTestSettings(max_violations=school.default_max_violations)
        test = OnlineTest(
            school_id=sid,
            teacher_id=teacher.id if teacher else None,
            created_by=user.id,
            class_id=body.class_id,
            subject_id=body.subject_id,
            syllabus_topic_id=body.syllabus_topic_id,
            title=body.title.strip(),
            instructions=body.instructions,
            status="DRAFT",
            duration_minutes=body.duration_minutes,
            opens_at=body.opens_at,
            closes_at=body.closes_at,
            total_points=0,
            settings=settings.model_dump(mode="json"),
        )
        self.db.add(test)
        await self.db.flush()
        await self._audit(sid, user.id, "TEST_CREATED", test_id=test.id, details={"title": test.title})
        return await self.get_test(user, test.id)

    async def list_tests(
        self, user: CurrentUser, *, status: str | None, class_id: UUID | None
    ) -> list[S.OnlineTestOut]:
        sid = self._sid(user)
        stmt = select(OnlineTest).where(OnlineTest.school_id == sid)
        if user.role == UserRole.TEACHER:
            teacher = await self._teacher(user)
            assigned = select(TeacherAssignment.class_id).where(TeacherAssignment.teacher_id == teacher.id)
            own_classes = select(ClassRoom.id).where(ClassRoom.class_teacher_id == teacher.id)
            stmt = stmt.where(
                or_(
                    OnlineTest.teacher_id == teacher.id,
                    OnlineTest.class_id.in_(assigned),
                    OnlineTest.class_id.in_(own_classes),
                ),
                or_(OnlineTest.status != "DRAFT", OnlineTest.teacher_id == teacher.id),
            )
        elif not self._is_admin(user):
            raise ForbiddenError("Not allowed")
        if status:
            stmt = stmt.where(OnlineTest.status == status.upper())
        if class_id:
            stmt = stmt.where(OnlineTest.class_id == class_id)
        tests = list((await self.db.execute(stmt.order_by(OnlineTest.created_at.desc()).limit(300))).scalars())
        counts = await self._counts([t.id for t in tests])
        now = await self._now()
        return [await self._test_out(t, now, counts[t.id]) for t in tests]

    async def get_test(self, user: CurrentUser, test_id: UUID) -> S.OnlineTestDetailOut:
        test = await self._manage_test(user, test_id, write=False)
        now = await self._now()
        base = await self._test_out(test, now)
        keys = await self._keys(test.id)
        qs = [self._q_teacher_out(q, keys.get(q.id)) for q in await self._questions(test.id)]
        return S.OnlineTestDetailOut(
            **base.model_dump(), questions=qs, has_attempts=await self._has_attempts(test.id)
        )

    async def update_test(self, user: CurrentUser, test_id: UUID, body: S.OnlineTestUpdate) -> S.OnlineTestDetailOut:
        test = await self._manage_test(user, test_id, write=True)
        await self._assert_editable(test)
        data = body.model_dump(exclude_unset=True)
        if "settings" in data and body.settings is not None:
            test.settings = body.settings.model_dump(mode="json")
        for field in ("title", "instructions", "duration_minutes", "opens_at", "closes_at"):
            if field in data:
                setattr(test, field, data[field])
        if test.opens_at and test.closes_at and test.closes_at <= test.opens_at:
            raise AppError("The closing time must be after the opening time", code="invalid_window", status_code=422)
        await self.db.flush()
        await self._audit(self._sid(user), user.id, "TEST_UPDATED", test_id=test.id, details={"fields": sorted(data)})
        return await self.get_test(user, test.id)

    async def delete_test(self, user: CurrentUser, test_id: UUID) -> None:
        test = await self._manage_test(user, test_id, write=True)
        if test.status != "DRAFT" or await self._has_attempts(test.id):
            raise ConflictError(
                "Only drafts with no attempts can be deleted. Close the test instead.", code="test_not_deletable"
            )
        await self._audit(self._sid(user), user.id, "TEST_DELETED", details={"title": test.title})
        await self.db.delete(test)
        await self.db.flush()

    async def clone_test(self, user: CurrentUser, test_id: UUID) -> S.OnlineTestDetailOut:
        src = await self._manage_test(user, test_id, write=False)
        sid = self._sid(user)
        await self.assert_enabled(sid)
        teacher = await self._assert_can_author(user, src.class_id, src.subject_id)
        copy = OnlineTest(
            school_id=sid,
            teacher_id=teacher.id if teacher else src.teacher_id,
            created_by=user.id,
            class_id=src.class_id,
            subject_id=src.subject_id,
            syllabus_topic_id=src.syllabus_topic_id,
            title=f"Copy of {src.title}"[:255],
            instructions=src.instructions,
            status="DRAFT",
            duration_minutes=src.duration_minutes,
            total_points=0,
            settings=dict(src.settings or {}),
        )
        self.db.add(copy)
        await self.db.flush()
        keys = await self._keys(src.id)
        for q in await self._questions(src.id):
            nq = OnlineTestQuestion(
                school_id=sid, test_id=copy.id, class_id=copy.class_id, prompt=q.prompt, options=list(q.options),
                points=q.points, sort_order=q.sort_order, difficulty=q.difficulty, source=q.source,
                ai_generation_id=q.ai_generation_id, ai_reviewed=q.ai_reviewed,
            )
            self.db.add(nq)
            await self.db.flush()
            key = keys.get(q.id)
            if key:
                self.db.add(
                    OnlineTestQuestionKey(
                        question_id=nq.id, school_id=sid, test_id=copy.id, class_id=copy.class_id,
                        correct_option_id=key.correct_option_id, explanation=key.explanation,
                    )
                )
        await self.db.flush()
        await self._recount(copy)
        await self._audit(sid, user.id, "TEST_CLONED", test_id=copy.id, details={"from": str(src.id)})
        return await self.get_test(user, copy.id)

    # ── questions ────────────────────────────────────────────

    async def _add_question(
        self, test: OnlineTest, item: S.QuestionIn, *, source: str, reviewed: bool, gen_id, order: int
    ) -> OnlineTestQuestion:
        options = [{"id": secrets.token_hex(3), "text": t} for t in item.options]
        q = OnlineTestQuestion(
            school_id=test.school_id, test_id=test.id, class_id=test.class_id, prompt=item.prompt,
            options=options, points=item.points, sort_order=order, difficulty=item.difficulty,
            source=source, ai_generation_id=gen_id, ai_reviewed=reviewed or source == "MANUAL",
        )
        self.db.add(q)
        await self.db.flush()
        self.db.add(
            OnlineTestQuestionKey(
                question_id=q.id, school_id=test.school_id, test_id=test.id, class_id=test.class_id,
                correct_option_id=options[item.correct_index]["id"], explanation=item.explanation,
            )
        )
        await self.db.flush()
        return q

    async def _next_order(self, test_id: UUID) -> int:
        top = (
            await self.db.execute(
                select(func.coalesce(func.max(OnlineTestQuestion.sort_order), -1)).where(
                    OnlineTestQuestion.test_id == test_id
                )
            )
        ).scalar_one()
        return int(top) + 1

    async def add_questions(
        self, user: CurrentUser, test_id: UUID, items: list[S.QuestionBulkItem], gen_id: UUID | None
    ) -> S.OnlineTestDetailOut:
        test = await self._manage_test(user, test_id, write=True)
        await self._assert_editable(test)
        order = await self._next_order(test.id)
        for offset, item in enumerate(items):
            await self._add_question(
                test, item, source=item.source, reviewed=item.reviewed, gen_id=gen_id, order=order + offset
            )
        await self._recount(test)
        await self._audit(
            self._sid(user), user.id, "QUESTIONS_ADDED", test_id=test.id,
            details={"count": len(items), "ai": gen_id is not None},
        )
        return await self.get_test(user, test.id)

    async def update_question(
        self, user: CurrentUser, test_id: UUID, qid: UUID, body: S.QuestionUpdate
    ) -> S.OnlineTestDetailOut:
        test = await self._manage_test(user, test_id, write=True)
        await self._assert_editable(test)
        q = await self.db.get(OnlineTestQuestion, qid)
        if not q or q.test_id != test.id:
            raise NotFoundError("Question not found")
        key = await self.db.get(OnlineTestQuestionKey, qid)
        data = body.model_dump(exclude_unset=True)
        if body.options is not None:
            if body.correct_index is None:
                raise AppError("Send correct_index together with options", code="correct_index_required", status_code=422)
            texts = [t.strip() for t in body.options]
            lowered = [t.lower() for t in texts]
            if len(set(lowered)) != len(lowered) or not 0 <= body.correct_index < len(texts) or any(not t for t in texts):
                raise AppError(
                    "Options must be unique and non-empty, with one correct answer",
                    code="invalid_options", status_code=422,
                )
            existing = {o["text"].strip().lower(): o["id"] for o in q.options}
            new_opts = [{"id": existing.get(t.lower()) or secrets.token_hex(3), "text": t} for t in texts]
            q.options = new_opts
            if key:
                key.correct_option_id = new_opts[body.correct_index]["id"]
        elif body.correct_index is not None:
            if not 0 <= body.correct_index < len(q.options):
                raise AppError("correct_index out of range", code="invalid_options", status_code=422)
            if key:
                key.correct_option_id = q.options[body.correct_index]["id"]
        if body.prompt is not None:
            q.prompt = body.prompt.strip()
        if body.points is not None:
            q.points = body.points
        if "difficulty" in data:
            q.difficulty = body.difficulty
        if "explanation" in data and key:
            key.explanation = body.explanation
        if q.source == "AI":
            q.source = "AI_EDITED"
        q.ai_reviewed = True  # the teacher touched it: that counts as review
        await self.db.flush()
        await self._recount(test)
        await self._audit(
            self._sid(user), user.id, "QUESTION_UPDATED", test_id=test.id, details={"question_id": str(qid)}
        )
        return await self.get_test(user, test.id)

    async def confirm_ai_questions(self, user: CurrentUser, test_id: UUID) -> S.OnlineTestDetailOut:
        test = await self._manage_test(user, test_id, write=True)
        await self._assert_editable(test)
        for q in await self._questions(test.id):
            if q.source in ("AI", "AI_EDITED"):
                q.ai_reviewed = True
        await self.db.flush()
        await self._audit(self._sid(user), user.id, "AI_QUESTIONS_CONFIRMED", test_id=test.id)
        return await self.get_test(user, test.id)

    async def delete_question(self, user: CurrentUser, test_id: UUID, qid: UUID) -> S.OnlineTestDetailOut:
        test = await self._manage_test(user, test_id, write=True)
        await self._assert_editable(test)
        q = await self.db.get(OnlineTestQuestion, qid)
        if not q or q.test_id != test.id:
            raise NotFoundError("Question not found")
        await self.db.delete(q)
        await self.db.flush()
        await self._recount(test)
        await self._audit(
            self._sid(user), user.id, "QUESTION_DELETED", test_id=test.id, details={"question_id": str(qid)}
        )
        return await self.get_test(user, test.id)

    async def reorder(self, user: CurrentUser, test_id: UUID, ids: list[UUID]) -> S.OnlineTestDetailOut:
        test = await self._manage_test(user, test_id, write=True)
        await self._assert_editable(test)
        qs = {q.id: q for q in await self._questions(test.id)}
        if set(ids) != set(qs) or len(ids) != len(qs):
            raise AppError("Send every question id exactly once", code="invalid_order", status_code=422)
        for i, qid in enumerate(ids):
            qs[qid].sort_order = i
        await self.db.flush()
        return await self.get_test(user, test.id)

    # ── publish / close / release ───────────────────────────

    async def publish(self, user: CurrentUser, test_id: UUID) -> S.OnlineTestDetailOut:
        sid = self._sid(user)
        test = await self._manage_test(user, test_id, write=True)
        await self.assert_enabled(sid)
        if test.status != "DRAFT":
            raise ConflictError("Only a draft can be published", code="not_draft")
        qs = await self._questions(test.id)
        if not qs:
            raise AppError("Add at least one question before publishing", code="no_questions", status_code=422)
        unreviewed = [q for q in qs if q.source in ("AI", "AI_EDITED") and not q.ai_reviewed]
        if unreviewed:
            raise AppError(
                f"{len(unreviewed)} AI-generated question(s) still need your review before publishing",
                code="ai_review_required", status_code=422,
            )
        keys = await self._keys(test.id)
        if any(q.id not in keys for q in qs):
            raise AppError("Every question needs a correct answer", code="missing_key", status_code=422)
        now = await self._now()
        if test.closes_at is None:
            raise AppError("Set when the test closes before publishing", code="closes_at_required", status_code=422)
        if test.closes_at <= now:
            raise AppError("The closing time is in the past", code="invalid_window", status_code=422)
        if test.opens_at is None:
            test.opens_at = now
        await self._recount(test)
        test.status = "PUBLISHED"
        test.published_at = now
        await self.db.flush()
        await self._audit(sid, user.id, "TEST_PUBLISHED", test_id=test.id, details={"questions": len(qs)})
        await self._notify_published(test)
        return await self.get_test(user, test.id)

    async def close(self, user: CurrentUser, test_id: UUID) -> S.OnlineTestDetailOut:
        sid = self._sid(user)
        test = await self._manage_test(user, test_id, write=True)
        if test.status == "DRAFT":
            raise ConflictError("A draft has nothing to close", code="not_published")
        now = await self._now()
        test.status = "CLOSED"
        if test.closes_at is None or test.closes_at > now:
            test.closes_at = now
        await self.db.flush()
        open_attempts = (
            await self.db.execute(
                select(OnlineTestAttempt).where(
                    OnlineTestAttempt.test_id == test.id, OnlineTestAttempt.status == IN_PROGRESS
                )
            )
        ).scalars().all()
        for attempt in open_attempts:
            await self.finalise(attempt, test, reason="TEACHER_CLOSED", now=now)
        await self._audit(sid, user.id, "TEST_CLOSED", test_id=test.id, details={"auto_submitted": len(open_attempts)})
        return await self.get_test(user, test.id)

    def _released(self, test: OnlineTest, now: datetime) -> bool:
        if test.results_released_at is not None:
            return True
        return (
            self._settings(test).result_release == S.ResultRelease.AFTER_CLOSE
            and test.status != "DRAFT"
            and (test.status == "CLOSED" or (test.closes_at is not None and now >= test.closes_at))
        )

    async def release_results(self, user: CurrentUser, test_id: UUID) -> S.ReleaseOut:
        test = await self._manage_test(user, test_id, write=True)
        if test.status == "DRAFT":
            raise ConflictError("Publish the test first", code="not_published")
        now = await self._now()
        if test.status == "PUBLISHED" and test.closes_at and now < test.closes_at:
            raise ConflictError("Results can be released after the test closes. Close it first.", code="still_open")
        return await self._release(test, now, actor=user.id)

    async def _release(self, test: OnlineTest, now: datetime, *, actor: UUID | None) -> S.ReleaseOut:
        if test.results_released_at is None:
            test.results_released_at = now
            test.results_released_by = actor
            await self.db.flush()
            await self._audit(test.school_id, actor, "RESULTS_RELEASED", test_id=test.id)
        notified = await self._notify_results(test)
        return S.ReleaseOut(test_id=test.id, released_at=test.results_released_at, notified=notified)

    # ── notifications ───────────────────────────────────────

    async def _enrolled_students(self, test: OnlineTest) -> list[Student]:
        return list(
            (
                await self.db.execute(
                    select(Student)
                    .join(Enrollment, Enrollment.student_id == Student.id)
                    .where(
                        Enrollment.school_id == test.school_id,
                        Enrollment.class_id == test.class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalars()
        )

    async def _parent_users(self, school_id: UUID, student_id: UUID) -> list[UUID]:
        # Teachers cannot read parent profiles under RLS; this lookup only returns user ids for notification.
        async with service_scope(self.db, school_id):
            rows = (
                await self.db.execute(
                    select(Parent.user_id)
                    .join(StudentParent, StudentParent.parent_id == Parent.id)
                    .where(StudentParent.school_id == school_id, StudentParent.student_id == student_id)
                )
            ).scalars()
            return list(rows)

    async def _notify(self, school_id, user_ids, *, title, body, category, link, key_prefix, entity_id, meta=None) -> int:
        seen: list[UUID] = []
        for uid in user_ids:
            if uid in seen:
                continue
            seen.append(uid)
            await create_in_app_notification(
                self.db, school_id=school_id, user_id=uid, title=title, body=body, category=category,
                link_path=link, meta=meta or {}, entity_type="online_test", entity_id=entity_id,
                event_key=f"{key_prefix}:{entity_id}:{uid}",
            )
        await send_push(self.db, school_id=school_id, user_ids=seen, title=title, body=body, data={"link": link})
        return len(seen)

    async def _notify_published(self, test: OnlineTest) -> None:
        subject = await self.db.get(Subject, test.subject_id)
        closes = test.closes_at.astimezone(timezone.utc).strftime("%d %b %H:%M UTC") if test.closes_at else "TBA"
        body = f"{subject.name if subject else 'New'} test: {test.title}. {test.duration_minutes} min, closes {closes}."
        students = await self._enrolled_students(test)
        await self._notify(
            test.school_id, [s.user_id for s in students], title="New online test", body=body,
            category="ONLINE_TEST_PUBLISHED", link="/student/online-tests", key_prefix="OT_PUBLISHED",
            entity_id=test.id,
        )
        for s in students:
            parents = await self._parent_users(test.school_id, s.id)
            name = await self._user_name(s.user_id) or "Your child"
            await self._notify(
                test.school_id, parents, title="New online test", body=f"{name}: {body}",
                category="ONLINE_TEST_PUBLISHED", link="/parent/online-tests",
                key_prefix=f"OT_PUBLISHED_{s.id}", entity_id=test.id, meta={"student_id": str(s.id)},
            )

    async def _notify_results(self, test: OnlineTest, only_student: Student | None = None) -> int:
        q = select(OnlineTestAttempt).where(
            OnlineTestAttempt.test_id == test.id,
            OnlineTestAttempt.status.in_(FINAL_STATES),
            OnlineTestAttempt.review_status != "PENDING",
        )
        if only_student:
            q = q.where(OnlineTestAttempt.student_id == only_student.id)
        count = 0
        for a in (await self.db.execute(q)).scalars().all():
            student = await self.db.get(Student, a.student_id)
            if not student:
                continue
            count += await self._notify(
                test.school_id, [student.user_id], title="Test result ready",
                body=f"Your result for {test.title} is ready.", category="ONLINE_TEST_RESULT",
                link="/student/online-tests", key_prefix="OT_RESULT", entity_id=test.id,
            )
            parents = await self._parent_users(test.school_id, student.id)
            name = await self._user_name(student.user_id) or "Your child"
            await self._notify(
                test.school_id, parents, title="Test result ready",
                body=f"{name}: result for {test.title} is ready.", category="ONLINE_TEST_RESULT",
                link="/parent/online-tests", key_prefix=f"OT_RESULT_{student.id}", entity_id=test.id,
                meta={"student_id": str(student.id)},
            )
        return count

    # ── student: list / start / answer / events / submit ────

    async def _active_class_ids(self, student: Student) -> list[UUID]:
        return list(
            (
                await self.db.execute(
                    select(Enrollment.class_id).where(
                        Enrollment.student_id == student.id, Enrollment.status == EnrollmentStatus.ACTIVE.value
                    )
                )
            ).scalars()
        )

    async def list_for_student(self, user: CurrentUser) -> list[S.StudentTestOut]:
        student = await self._student(user)
        sid = self._sid(user)
        school = await self.school_settings(sid)
        if not school.enabled:
            return []
        classes = await self._active_class_ids(student)
        if not classes:
            return []
        tests = list(
            (
                await self.db.execute(
                    select(OnlineTest)
                    .where(
                        OnlineTest.school_id == sid,
                        OnlineTest.class_id.in_(classes),
                        OnlineTest.status.in_(("PUBLISHED", "CLOSED")),
                    )
                    .order_by(OnlineTest.opens_at.desc().nullslast())
                )
            ).scalars()
        )
        return await self._student_rows(tests, student)

    async def _student_rows(self, tests: list[OnlineTest], student: Student) -> list[S.StudentTestOut]:
        if not tests:
            return []
        now = await self._now()
        counts = await self._counts([t.id for t in tests])
        attempts = {
            a.test_id: a
            for a in (
                await self.db.execute(
                    select(OnlineTestAttempt).where(
                        OnlineTestAttempt.student_id == student.id,
                        OnlineTestAttempt.test_id.in_([t.id for t in tests]),
                    )
                )
            ).scalars()
        }
        out = []
        for t in tests:
            a = attempts.get(t.id)
            if a and a.status == IN_PROGRESS and now > a.deadline_at + timedelta(seconds=GRACE_SECONDS):
                await self.finalise(a, t, reason="TIME", now=now)
            show = bool(a) and self._result_visible(t, a, now)
            settings = self._settings(t)
            classroom = await self.db.get(ClassRoom, t.class_id)
            subject = await self.db.get(Subject, t.subject_id)
            out.append(
                S.StudentTestOut(
                    id=t.id, title=t.title, subject_name=subject.name if subject else None,
                    class_name=classroom.name if classroom else None, instructions=t.instructions,
                    duration_minutes=t.duration_minutes, opens_at=t.opens_at, closes_at=t.closes_at,
                    question_count=counts[t.id]["q"], total_points=t.total_points,
                    effective_status=self._effective(t, now),
                    attempt_status=a.status if a else None, attempt_id=a.id if a else None,
                    results_available=show, score=a.score if show else None,
                    max_score=a.max_score if show else None, percentage=a.percentage if show else None,
                    grade_label=a.grade_label if show else None,
                    secure_mode=settings.secure_mode, max_violations=settings.max_violations,
                )
            )
        return out

    def _result_visible(self, test: OnlineTest, a: OnlineTestAttempt, now: datetime) -> bool:
        return a.status in FINAL_STATES and a.review_status != "PENDING" and self._released(test, now)

    @staticmethod
    def _shuffled(ids: list, enabled: bool) -> list:
        out = list(ids)
        if enabled:
            _rng.shuffle(out)
        return out

    async def _start_payload(
        self, test: OnlineTest, a: OnlineTestAttempt, now: datetime, resumed: bool
    ) -> S.AttemptStartOut:
        qs = {q.id: q for q in await self._questions(test.id)}
        rows = (await self.db.execute(select(OnlineTestAnswer).where(OnlineTestAnswer.attempt_id == a.id))).scalars().all()
        answers = {str(x.question_id): x.selected_option_id for x in rows}
        flagged = [x.question_id for x in rows if x.flagged]
        questions = []
        for qid_s in a.question_order:
            q = qs.get(UUID(qid_s))
            if not q:
                continue
            by_id = {o["id"]: o for o in q.options}
            order = (a.option_order or {}).get(qid_s) or [o["id"] for o in q.options]
            questions.append(
                S.StudentQuestionOut(
                    id=q.id, prompt=q.prompt, points=q.points,
                    options=[S.OptionOut(**by_id[i]) for i in order if i in by_id],
                )
            )
        s = self._settings(test)
        return S.AttemptStartOut(
            attempt_id=a.id, test_id=test.id, title=test.title, instructions=test.instructions, status=a.status,
            resumed=resumed, started_at=a.started_at, deadline_at=a.deadline_at, server_now=now,
            duration_minutes=test.duration_minutes, secure_mode=s.secure_mode, max_violations=s.max_violations,
            violation_count=a.violation_count, questions=questions, answers=answers, flagged=flagged,
        )

    async def _attempt_for(self, test_id: UUID, student_id: UUID) -> OnlineTestAttempt | None:
        return (
            await self.db.execute(
                select(OnlineTestAttempt).where(
                    OnlineTestAttempt.test_id == test_id, OnlineTestAttempt.student_id == student_id
                )
            )
        ).scalar_one_or_none()

    async def start_attempt(self, user: CurrentUser, test_id: UUID, body: S.AttemptStartIn) -> S.AttemptStartOut:
        sid = self._sid(user)
        student = await self._student(user)
        await self.assert_enabled(sid)
        test = await self._get_test(sid, test_id)
        if test.status == "DRAFT" or test.class_id not in await self._active_class_ids(student):
            raise NotFoundError("Test not found")
        now = await self._now()
        existing = await self._attempt_for(test.id, student.id)
        if existing:
            return await self._resume(existing, test, now)
        if test.status != "PUBLISHED":
            raise ConflictError("This test is closed", code="test_closed")
        if test.opens_at and now < test.opens_at:
            raise ConflictError("This test has not opened yet", code="not_open")
        if test.closes_at and now >= test.closes_at:
            raise ConflictError("This test is closed", code="test_closed")
        async with service_scope(self.db, sid):  # the student has no attempt yet, so RLS hides the questions
            qs = await self._questions(test.id)
        if not qs:
            raise ConflictError("This test has no questions", code="no_questions")
        s = self._settings(test)
        q_order = self._shuffled([str(q.id) for q in qs], s.shuffle_questions)
        o_order = {str(q.id): self._shuffled([o["id"] for o in q.options], s.shuffle_options) for q in qs}
        deadline = now + timedelta(minutes=test.duration_minutes)
        if test.closes_at and test.closes_at < deadline:
            deadline = test.closes_at
        info = body.client_info if body.client_info and len(str(body.client_info)) < 800 else {}
        attempt = OnlineTestAttempt(
            school_id=sid, test_id=test.id, student_id=student.id, class_id=test.class_id, status=IN_PROGRESS,
            started_at=now, deadline_at=deadline, last_seen_at=now, question_order=q_order, option_order=o_order,
            violation_count=0, resume_count=0, review_status="NONE", client_info=info,
        )
        try:
            # begin_nested() re-applies the tenant GUCs (after_begin), so the scope must open inside it.
            async with self.db.begin_nested():
                async with service_scope(self.db, sid):
                    self.db.add(attempt)
                    await self.db.flush()
        except IntegrityError:
            again = await self._attempt_for(test.id, student.id)
            if again is None:
                raise
            return await self._resume(again, test, now)
        await self._audit(sid, user.id, "ATTEMPT_STARTED", test_id=test.id, attempt_id=attempt.id)
        if body.fullscreen_supported is False:
            await self._log_events(attempt, [S.EventIn(type="FULLSCREEN_UNSUPPORTED")], now, count=False)
        return await self._start_payload(test, attempt, now, resumed=False)

    async def _resume(self, a: OnlineTestAttempt, test: OnlineTest, now: datetime) -> S.AttemptStartOut:
        if a.status != IN_PROGRESS:
            raise ConflictError("You have already taken this test", code="already_attempted")
        if now > a.deadline_at + timedelta(seconds=GRACE_SECONDS):
            await self.finalise(a, test, reason="TIME", now=now)
            raise ConflictError("Your time for this test has ended. Your answers were submitted.", code="attempt_expired")
        s = self._settings(test)
        async with service_scope(self.db, a.school_id):
            a.resume_count += 1
            a.last_seen_at = now
            if a.resume_count > s.max_resumes and a.review_status == "NONE":
                a.review_status = "PENDING"
                a.review_note = "Resumed more often than allowed"
            await self.db.flush()
        await self._log_events(
            a, [S.EventIn(type="NETWORK_LOST", meta={"resume": a.resume_count})], now, count=False, label="RESUME"
        )
        return await self._start_payload(test, a, now, resumed=True)

    async def _open_attempt(self, user: CurrentUser, attempt_id: UUID) -> tuple[OnlineTestAttempt, OnlineTest, datetime]:
        sid = self._sid(user)
        student = await self._student(user)
        a = await self.db.get(OnlineTestAttempt, attempt_id)
        if not a or a.school_id != sid or a.student_id != student.id:
            raise NotFoundError("Attempt not found")
        test = await self._get_test(sid, a.test_id)
        now = await self._now()
        if a.status != IN_PROGRESS:
            raise ConflictError("This attempt is already submitted", code="attempt_closed")
        if now > a.deadline_at + timedelta(seconds=GRACE_SECONDS):
            await self.finalise(a, test, reason="TIME", now=now)
            raise ConflictError("Time is up. Your answers were submitted.", code="attempt_expired")
        return a, test, now

    async def save_answer(self, user: CurrentUser, attempt_id: UUID, qid: UUID, body: S.AnswerIn) -> S.AnswerSavedOut:
        a, _test, now = await self._open_attempt(user, attempt_id)
        q = await self.db.get(OnlineTestQuestion, qid)
        if not q or q.test_id != a.test_id:
            raise NotFoundError("Question not found")
        if body.selected_option_id is not None and body.selected_option_id not in {o["id"] for o in q.options}:
            raise AppError("That option does not belong to this question", code="invalid_option", status_code=422)
        row = (
            await self.db.execute(
                select(OnlineTestAnswer).where(
                    OnlineTestAnswer.attempt_id == a.id, OnlineTestAnswer.question_id == qid
                )
            )
        ).scalar_one_or_none()
        if row:
            row.selected_option_id = body.selected_option_id
            row.flagged = body.flagged
            row.answered_at = now
        else:
            self.db.add(
                OnlineTestAnswer(
                    school_id=a.school_id, attempt_id=a.id, test_id=a.test_id, student_id=a.student_id,
                    class_id=a.class_id, question_id=qid, selected_option_id=body.selected_option_id,
                    flagged=body.flagged, answered_at=now,
                )
            )
        await self.db.flush()
        return S.AnswerSavedOut(
            question_id=qid, selected_option_id=body.selected_option_id, server_now=now, deadline_at=a.deadline_at
        )

    def _state(self, a: OnlineTestAttempt, test: OnlineTest, now: datetime, *, auto: bool = False) -> S.AttemptStateOut:
        s = self._settings(test)
        warnings_left = None
        if s.secure_mode and s.max_violations > 0:
            warnings_left = max(0, s.max_violations - a.violation_count)
        return S.AttemptStateOut(
            attempt_id=a.id, status=a.status, server_now=now, deadline_at=a.deadline_at,
            seconds_left=max(0, int((a.deadline_at - now).total_seconds())),
            violation_count=a.violation_count, max_violations=s.max_violations, warnings_left=warnings_left,
            auto_submitted=auto or a.status == AUTO_SUBMITTED, submit_reason=a.submit_reason,
        )

    async def _log_events(
        self, a: OnlineTestAttempt, events: list[S.EventIn], now: datetime, *, count: bool, label: str | None = None
    ) -> int:
        counted = 0
        async with service_scope(self.db, a.school_id):  # students cannot read the event log themselves
            last = (
                await self.db.execute(
                    select(func.max(OnlineTestEvent.server_at)).where(
                        OnlineTestEvent.attempt_id == a.id, OnlineTestEvent.counted.is_(True)
                    )
                )
            ).scalar_one_or_none()
        for ev in events:
            is_counted = count and ev.type in COUNTED_EVENTS
            if is_counted and last is not None and (now - last).total_seconds() < DEDUPE_SECONDS:
                is_counted = False  # same incident reported by two browser events
            meta = dict(ev.meta or {})
            if label:
                meta["label"] = label
            self.db.add(
                OnlineTestEvent(
                    school_id=a.school_id, attempt_id=a.id, test_id=a.test_id, student_id=a.student_id,
                    class_id=a.class_id, type=ev.type, counted=is_counted, client_at=ev.client_at,
                    server_at=now, meta=meta or None,
                )
            )
            if is_counted:
                counted += 1
                last = now
        await self.db.flush()
        return counted

    async def record_events(self, user: CurrentUser, attempt_id: UUID, body: S.EventsIn) -> S.AttemptStateOut:
        a, test, now = await self._open_attempt(user, attempt_id)
        s = self._settings(test)
        added = await self._log_events(a, body.events, now, count=s.secure_mode)
        auto = False
        if added:
            async with service_scope(self.db, a.school_id):
                a.violation_count += added
                await self.db.flush()
            if s.secure_mode and s.max_violations > 0 and a.violation_count >= s.max_violations:
                await self.finalise(a, test, reason="VIOLATIONS", now=now)
                auto = True
        return self._state(a, test, now, auto=auto)

    async def heartbeat(self, user: CurrentUser, attempt_id: UUID) -> S.AttemptStateOut:
        a, test, now = await self._open_attempt(user, attempt_id)
        gap = (now - a.last_seen_at).total_seconds() if a.last_seen_at else 0
        async with service_scope(self.db, a.school_id):
            a.last_seen_at = now
            await self.db.flush()
        if gap > 45:
            await self._log_events(a, [S.EventIn(type="NETWORK_LOST", meta={"gap_seconds": int(gap)})], now, count=False)
        return self._state(a, test, now)

    async def submit(self, user: CurrentUser, attempt_id: UUID) -> S.SubmitOut:
        sid = self._sid(user)
        student = await self._student(user)
        a = await self.db.get(OnlineTestAttempt, attempt_id)
        if not a or a.school_id != sid or a.student_id != student.id:
            raise NotFoundError("Attempt not found")
        test = await self._get_test(sid, a.test_id)
        now = await self._now()
        if a.status == IN_PROGRESS:
            expired = now > a.deadline_at + timedelta(seconds=GRACE_SECONDS)
            await self.finalise(a, test, reason="TIME" if expired else "MANUAL", now=now)
        return self._submit_out(a)

    @staticmethod
    def _submit_out(a: OnlineTestAttempt) -> S.SubmitOut:
        msg = "Your test was submitted."
        if a.submit_reason == "TIME":
            msg = "Time was up, so your test was submitted automatically."
        elif a.submit_reason == "VIOLATIONS":
            msg = "Your test was submitted because you left the test screen too many times. Your teacher will review it."
        elif a.submit_reason == "TEACHER_CLOSED":
            msg = "Your teacher closed the test, so it was submitted."
        return S.SubmitOut(
            attempt_id=a.id, status=a.status, submit_reason=a.submit_reason,
            held_for_review=a.review_status == "PENDING", message=msg,
        )

    # ── grading ──────────────────────────────────────────────

    async def _grade_label(self, school_id: UUID, pct: float) -> str | None:
        scale = (
            await self.db.execute(
                select(GradingScale)
                .where(
                    GradingScale.school_id == school_id,
                    GradingScale.is_default.is_(True),
                    GradingScale.is_active.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if not scale:
            return None
        rule = (
            await self.db.execute(
                select(GradeRule)
                .where(
                    GradeRule.grading_scale_id == scale.id,
                    GradeRule.min_percentage <= pct,
                    GradeRule.max_percentage >= pct,
                )
                .order_by(GradeRule.min_percentage.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return rule.grade if rule else None

    async def finalise(self, a: OnlineTestAttempt, test: OnlineTest, *, reason: str, now: datetime) -> None:
        """Grade and close an attempt. Idempotent. Reads the answer key only inside service scope."""
        if a.status != IN_PROGRESS:
            return
        async with service_scope(self.db, a.school_id):
            questions = await self._questions(test.id)
            keys = await self._keys(test.id)
            rows = (await self.db.execute(select(OnlineTestAnswer).where(OnlineTestAnswer.attempt_id == a.id))).scalars()
            answers = {x.question_id: x.selected_option_id for x in rows}
            score = 0.0
            maximum = 0.0
            for q in questions:
                maximum += q.points
                key = keys.get(q.id)
                if key and answers.get(q.id) == key.correct_option_id:
                    score += q.points
            pct = round(score * 100.0 / maximum, 2) if maximum else 0.0
            a.score = score
            a.max_score = maximum
            a.percentage = pct
            a.grade_label = await self._grade_label(a.school_id, pct)
            a.status = SUBMITTED if reason == "MANUAL" else AUTO_SUBMITTED
            a.submit_reason = reason
            a.submitted_at = min(now, a.deadline_at) if reason == "TIME" else now
            if reason == "VIOLATIONS" and a.review_status == "NONE":
                a.review_status = "PENDING"
                a.review_note = "Auto-submitted after repeated violations"
            elif a.resume_count > self._settings(test).max_resumes and a.review_status == "NONE":
                a.review_status = "PENDING"
                a.review_note = "Resumed more often than allowed"
            await self.db.flush()
        await self._audit(
            a.school_id, None, "ATTEMPT_SUBMITTED", test_id=test.id, attempt_id=a.id,
            details={"reason": reason, "score": a.score},
        )
        student = await self.db.get(Student, a.student_id)
        if a.review_status == "PENDING" and test.created_by:
            name = (await self._user_name(student.user_id)) if student else "A student"
            await self._notify(
                a.school_id, [test.created_by], title="Attempt needs review",
                body=f"{name}'s attempt on {test.title} was flagged. Review it before results are released.",
                category="ONLINE_TEST_FLAGGED", link=f"/teacher/online-tests/detail?id={test.id}",
                key_prefix="OT_FLAGGED", entity_id=a.id,
            )
        if test.results_released_at is not None and a.review_status != "PENDING" and student:
            await self._notify_results(test, only_student=student)

    # ── student result ───────────────────────────────────────

    async def student_result(self, user: CurrentUser, attempt_id: UUID) -> S.StudentResultOut:
        sid = self._sid(user)
        student = await self._student(user)
        a = await self.db.get(OnlineTestAttempt, attempt_id)
        if not a or a.school_id != sid or a.student_id != student.id:
            raise NotFoundError("Attempt not found")
        test = await self._get_test(sid, a.test_id)
        now = await self._now()
        if a.status == IN_PROGRESS and now > a.deadline_at + timedelta(seconds=GRACE_SECONDS):
            await self.finalise(a, test, reason="TIME", now=now)
        if a.status == IN_PROGRESS:
            raise ConflictError("Finish the test first", code="attempt_in_progress")
        if a.status == VOID or not self._result_visible(test, a, now):
            return S.StudentResultOut(
                attempt_id=a.id, test_id=test.id, title=test.title, released=False,
                held_for_review=a.review_status == "PENDING", status=a.status, submitted_at=a.submitted_at,
            )
        questions = await self._review_questions(a, test) if self._settings(test).show_answers else []
        return S.StudentResultOut(
            attempt_id=a.id, test_id=test.id, title=test.title, released=True, status=a.status,
            score=a.score, max_score=a.max_score, percentage=a.percentage, grade_label=a.grade_label,
            submitted_at=a.submitted_at, questions=questions,
        )

    async def _review_questions(self, a: OnlineTestAttempt, test: OnlineTest) -> list[S.ReviewedQuestionOut]:
        """Per-question review. Reads keys in service scope: only called after release or by staff."""
        async with service_scope(self.db, a.school_id):
            qs = {q.id: q for q in await self._questions(test.id)}
            keys = await self._keys(test.id)
            rows = (await self.db.execute(select(OnlineTestAnswer).where(OnlineTestAnswer.attempt_id == a.id))).scalars()
            answers = {x.question_id: x.selected_option_id for x in rows}
        out = []
        for qid_s in a.question_order:
            q = qs.get(UUID(qid_s))
            if not q:
                continue
            key = keys.get(q.id)
            sel = answers.get(q.id)
            correct = key.correct_option_id if key else None
            by_id = {o["id"]: o for o in q.options}
            order = (a.option_order or {}).get(qid_s) or list(by_id)
            ok = bool(sel) and sel == correct
            out.append(
                S.ReviewedQuestionOut(
                    id=q.id, prompt=q.prompt, options=[S.OptionOut(**by_id[i]) for i in order if i in by_id],
                    selected_option_id=sel, correct_option_id=correct, is_correct=ok,
                    explanation=key.explanation if key else None, points=q.points,
                    points_earned=float(q.points) if ok else 0.0,
                )
            )
        return out

    # ── teacher monitoring / review / analytics ─────────────

    async def _sweep_expired(self, test: OnlineTest, now: datetime) -> None:
        rows = (
            await self.db.execute(
                select(OnlineTestAttempt).where(
                    OnlineTestAttempt.test_id == test.id,
                    OnlineTestAttempt.status == IN_PROGRESS,
                    OnlineTestAttempt.deadline_at < now - timedelta(seconds=GRACE_SECONDS),
                )
            )
        ).scalars().all()
        for a in rows:
            await self.finalise(a, test, reason="TIME", now=now)

    async def attempts_overview(self, user: CurrentUser, test_id: UUID) -> list[S.AttemptRowOut]:
        test = await self._manage_test(user, test_id, write=False)
        await self._sweep_expired(test, await self._now())
        rows = (
            await self.db.execute(
                select(Student, User.full_name, OnlineTestAttempt)
                .join(
                    Enrollment,
                    and_(
                        Enrollment.student_id == Student.id,
                        Enrollment.class_id == test.class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    ),
                )
                .join(User, User.id == Student.user_id)
                .outerjoin(
                    OnlineTestAttempt,
                    and_(OnlineTestAttempt.student_id == Student.id, OnlineTestAttempt.test_id == test.id),
                )
                .order_by(User.full_name)
            )
        ).all()
        return [self._row(s, name, a) for s, name, a in rows]

    @staticmethod
    def _row(s: Student, name: str | None, a: OnlineTestAttempt | None) -> S.AttemptRowOut:
        return S.AttemptRowOut(
            attempt_id=a.id if a else None, student_id=s.id, student_name=name,
            roll_number=s.admission_no or s.student_code, status=a.status if a else "NOT_STARTED",
            started_at=a.started_at if a else None, submitted_at=a.submitted_at if a else None,
            submit_reason=a.submit_reason if a else None, violation_count=a.violation_count if a else 0,
            resume_count=a.resume_count if a else 0, score=a.score if a else None,
            max_score=a.max_score if a else None, percentage=a.percentage if a else None,
            grade_label=a.grade_label if a else None, review_status=a.review_status if a else "NONE",
            review_note=a.review_note if a else None, last_seen_at=a.last_seen_at if a else None,
        )

    async def attempt_detail(self, user: CurrentUser, attempt_id: UUID) -> S.AttemptDetailOut:
        a = await self.db.get(OnlineTestAttempt, attempt_id)
        if not a or a.school_id != self._sid(user):
            raise NotFoundError("Attempt not found")
        test = await self._manage_test(user, a.test_id, write=False)
        student = await self.db.get(Student, a.student_id)
        name = await self._user_name(student.user_id) if student else None
        events = (
            await self.db.execute(
                select(OnlineTestEvent).where(OnlineTestEvent.attempt_id == a.id).order_by(OnlineTestEvent.server_at)
            )
        ).scalars().all()
        base = self._row(student, name, a)
        answers = await self._review_questions(a, test) if a.status != IN_PROGRESS else []
        return S.AttemptDetailOut(
            **base.model_dump(),
            events=[
                S.EventOut(
                    id=e.id, type=e.type, counted=e.counted, client_at=e.client_at, server_at=e.server_at, meta=e.meta
                )
                for e in events
            ],
            answers=answers,
        )

    async def review_attempt(self, user: CurrentUser, attempt_id: UUID, body: S.ReviewIn) -> S.AttemptDetailOut:
        sid = self._sid(user)
        a = await self.db.get(OnlineTestAttempt, attempt_id)
        if not a or a.school_id != sid:
            raise NotFoundError("Attempt not found")
        test = await self._manage_test(user, a.test_id, write=True)
        now = await self._now()
        if body.decision == "ACCEPT":
            if a.status == IN_PROGRESS:
                raise ConflictError("The student is still taking this test", code="attempt_in_progress")
            a.review_status = "CLEARED"
        elif body.decision == "VOID":
            a.status = VOID
            a.review_status = "CLEARED"
        else:  # REOPEN
            if a.status not in FINAL_STATES:
                raise ConflictError("Only a submitted attempt can be reopened", code="not_submitted")
            if test.status == "CLOSED" and not body.extra_minutes:
                raise ConflictError("The test is closed. Give extra minutes to reopen it.", code="test_closed")
            left = int(((a.deadline_at - (a.submitted_at or now)).total_seconds()) // 60)
            minutes = body.extra_minutes or max(5, left)
            a.status = IN_PROGRESS
            a.deadline_at = now + timedelta(minutes=minutes)
            a.submitted_at = None
            a.submit_reason = None
            a.score = a.max_score = a.percentage = a.grade_label = None
            a.violation_count = 0
            a.review_status = "CLEARED"
        a.review_note = body.note or a.review_note
        a.reviewed_by = user.id
        a.reviewed_at = now
        await self.db.flush()
        await self._audit(
            sid, user.id, f"ATTEMPT_{body.decision}", test_id=test.id, attempt_id=a.id, details={"note": body.note}
        )
        if body.decision == "ACCEPT" and test.results_released_at is not None:
            await self._notify_results(test, only_student=await self.db.get(Student, a.student_id))
        return await self.attempt_detail(user, a.id)

    async def analytics(self, user: CurrentUser, test_id: UUID) -> S.AnalyticsOut:
        test = await self._manage_test(user, test_id, write=False)
        enrolled = len(await self._enrolled_students(test))
        attempts = list(
            (await self.db.execute(select(OnlineTestAttempt).where(OnlineTestAttempt.test_id == test.id))).scalars()
        )
        graded = [a for a in attempts if a.status in FINAL_STATES and a.percentage is not None]
        pcts = [a.percentage for a in graded]
        dist = [0] * 10
        for p in pcts:
            dist[min(9, int(p // 10))] += 1
        minutes = [(a.submitted_at - a.started_at).total_seconds() / 60 for a in graded if a.submitted_at]
        keys = await self._keys(test.id)
        answers = list(
            (await self.db.execute(select(OnlineTestAnswer).where(OnlineTestAnswer.test_id == test.id))).scalars()
        )
        final_ids = {a.id for a in graded}
        stats = []
        for q in await self._questions(test.id):
            qa = [x for x in answers if x.question_id == q.id and x.attempt_id in final_ids and x.selected_option_id]
            key = keys.get(q.id)
            correct = sum(1 for x in qa if key and x.selected_option_id == key.correct_option_id)
            counts: dict[str, int] = {}
            for x in qa:
                counts[x.selected_option_id] = counts.get(x.selected_option_id, 0) + 1
            stats.append(
                S.QuestionStat(
                    question_id=q.id, prompt=q.prompt, answered=len(qa), correct=correct,
                    percent_correct=round(correct * 100 / len(qa), 1) if qa else None, option_counts=counts,
                )
            )
        return S.AnalyticsOut(
            test_id=test.id, enrolled=enrolled, started=len(attempts), submitted=len(graded),
            held_for_review=sum(1 for a in attempts if a.review_status == "PENDING"),
            average_percentage=round(sum(pcts) / len(pcts), 1) if pcts else None,
            median_percentage=round(statistics.median(pcts), 1) if pcts else None,
            highest=max(pcts) if pcts else None, lowest=min(pcts) if pcts else None,
            average_minutes=round(sum(minutes) / len(minutes), 1) if minutes else None,
            distribution=dist, violations_total=sum(a.violation_count for a in attempts), questions=stats,
        )

    async def results_csv(self, user: CurrentUser, test_id: UUID) -> str:
        rows = await self.attempts_overview(user, test_id)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            ["Student", "Roll/Admission no", "Status", "Score", "Max", "Percentage", "Grade", "Violations", "Review",
             "Submitted at (UTC)"]
        )
        for r in rows:
            w.writerow(
                [
                    _csv_safe(r.student_name), _csv_safe(r.roll_number), r.status,
                    r.score if r.score is not None else "", r.max_score if r.max_score is not None else "",
                    r.percentage if r.percentage is not None else "", _csv_safe(r.grade_label), r.violation_count,
                    r.review_status,
                    r.submitted_at.astimezone(timezone.utc).isoformat() if r.submitted_at else "",
                ]
            )
        await self._audit(self._sid(user), user.id, "RESULTS_EXPORTED", test_id=test_id)
        return buf.getvalue()

    # ── parent ───────────────────────────────────────────────

    async def _parent_children(self, user: CurrentUser) -> list[Student]:
        sid = self._sid(user)
        parent = (
            await self.db.execute(select(Parent).where(Parent.school_id == sid, Parent.user_id == user.id))
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        return list(
            (
                await self.db.execute(
                    select(Student)
                    .join(StudentParent, StudentParent.student_id == Student.id)
                    .where(StudentParent.school_id == sid, StudentParent.parent_id == parent.id)
                )
            ).scalars()
        )

    async def parent_children(self, user: CurrentUser) -> list[S.ChildBriefOut]:
        out = []
        for s in await self._parent_children(user):
            classes = await self._active_class_ids(s)
            classroom = await self.db.get(ClassRoom, classes[0]) if classes else None
            out.append(
                S.ChildBriefOut(
                    student_id=s.id, name=await self._user_name(s.user_id),
                    class_name=classroom.name if classroom else None,
                )
            )
        return out

    async def parent_tests(self, user: CurrentUser, student_id: UUID) -> list[S.ChildTestOut]:
        sid = self._sid(user)
        child = next((c for c in await self._parent_children(user) if c.id == student_id), None)
        if not child:
            raise ForbiddenError("This student is not linked to your account")
        if not (await self.school_settings(sid)).enabled:
            return []
        classes = await self._active_class_ids(child)
        tests = list(
            (
                await self.db.execute(
                    select(OnlineTest)
                    .where(
                        OnlineTest.school_id == sid,
                        OnlineTest.class_id.in_(classes),
                        OnlineTest.status.in_(("PUBLISHED", "CLOSED")),
                    )
                    .order_by(OnlineTest.opens_at.desc().nullslast())
                )
            ).scalars()
        ) if classes else []
        rows = await self._student_rows(tests, child)
        return [
            S.ChildTestOut(
                test_id=r.id, title=r.title, subject_name=r.subject_name, class_name=r.class_name,
                opens_at=r.opens_at, closes_at=r.closes_at, effective_status=r.effective_status,
                attempt_status=r.attempt_status, released=r.results_available, score=r.score,
                max_score=r.max_score, percentage=r.percentage, grade_label=r.grade_label,
            )
            for r in rows
        ]

    # ── AI drafts ────────────────────────────────────────────

    async def ai_generate(self, user: CurrentUser, test_id: UUID, body: S.AiGenerateIn) -> S.AiGenerateOut:
        from src.modules.online_tests.ai_client import generate_mcq_drafts

        sid = self._sid(user)
        test = await self._manage_test(user, test_id, write=True)
        school = await self.assert_enabled(sid)
        await self._assert_editable(test)
        used = await self._ai_used(sid)
        if used + body.count > school.ai_monthly_cap:
            raise AppError(
                f"Your school's monthly AI question limit ({school.ai_monthly_cap}) would be exceeded. "
                f"{max(0, school.ai_monthly_cap - used)} left this month.",
                code="ai_cap_reached", status_code=429,
            )
        subject = await self.db.get(Subject, test.subject_id)
        classroom = await self.db.get(ClassRoom, test.class_id)
        existing = [q.prompt for q in await self._questions(test.id)]
        result = await generate_mcq_drafts(
            user,
            {
                "topic": body.topic, "subject_name": subject.name if subject else None,
                "class_label": classroom.name if classroom else None, "difficulty": body.difficulty,
                "count": body.count, "options_per_question": body.options_per_question,
                "language": body.language, "source_notes": body.source_notes,
                "avoid_prompts": existing[:100], "verify": body.verify,
            },
        )
        drafts = [S.AiDraftOut(**d) for d in result.get("questions", [])]
        usage = OnlineTestAiUsage(
            school_id=sid, teacher_user_id=user.id, test_id=test.id,
            period=datetime.now(timezone.utc).strftime("%Y-%m"), requested=body.count, returned=len(drafts),
        )
        self.db.add(usage)
        await self.db.flush()
        await self._audit(
            sid, user.id, "AI_GENERATED", test_id=test.id,
            details={"topic": body.topic[:80], "requested": body.count, "returned": len(drafts)},
        )
        return S.AiGenerateOut(
            ai_generation_id=usage.id, drafts=drafts, dropped_invalid=int(result.get("dropped_invalid", 0)),
            dropped_duplicates=int(result.get("dropped_duplicates", 0)), used_this_month=used + len(drafts),
            monthly_cap=school.ai_monthly_cap,
        )


class OnlineTestMaintenance:
    """Worker-driven sweeper (internal token endpoint).

    Listing runs with RLS bypass across schools; each item is then processed with the tenant bound to its own
    school (role SERVICE), because notification writes must still match the session's school.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.svc = OnlineTestService(db)

    async def _scan(self, stmt):
        await set_rls_bypass(self.db, True)
        return (await self.db.execute(stmt)).all()

    async def _enter(self, school_id: UUID) -> None:
        await bind_tenant(self.db, school_id, role="SERVICE")

    async def run(self) -> dict:
        await set_rls_bypass(self.db, True)
        now = await self.svc._now()
        finalised = closed = released = reminders = 0

        stale = await self._scan(
            select(OnlineTestAttempt.id, OnlineTestAttempt.test_id, OnlineTestAttempt.school_id)
            .where(
                OnlineTestAttempt.status == IN_PROGRESS,
                OnlineTestAttempt.deadline_at < now - timedelta(seconds=GRACE_SECONDS),
            )
            .limit(500)
        )
        for attempt_id, test_id, school_id in stale:
            await self._enter(school_id)
            a = await self.db.get(OnlineTestAttempt, attempt_id)
            test = await self.db.get(OnlineTest, test_id)
            if a and test:
                await self.svc.finalise(a, test, reason="TIME", now=now)
                finalised += 1

        past = await self._scan(
            select(OnlineTest.id, OnlineTest.school_id).where(
                OnlineTest.status == "PUBLISHED", OnlineTest.closes_at.is_not(None), OnlineTest.closes_at < now
            )
        )
        for test_id, school_id in past:
            await self._enter(school_id)
            open_one = (
                await self.db.execute(
                    select(OnlineTestAttempt.id)
                    .where(OnlineTestAttempt.test_id == test_id, OnlineTestAttempt.status == IN_PROGRESS)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if open_one is None:
                test = await self.db.get(OnlineTest, test_id)
                if test:
                    test.status = "CLOSED"
                    closed += 1
        await self.db.flush()

        pending = await self._scan(
            select(OnlineTest.id, OnlineTest.school_id).where(
                OnlineTest.status == "CLOSED", OnlineTest.results_released_at.is_(None)
            )
        )
        for test_id, school_id in pending:
            await self._enter(school_id)
            test = await self.db.get(OnlineTest, test_id)
            if test and self.svc._settings(test).result_release == S.ResultRelease.AFTER_CLOSE:
                await self.svc._release(test, now, actor=None)
                released += 1

        soon = await self._scan(
            select(OnlineTest.id, OnlineTest.school_id).where(
                OnlineTest.status == "PUBLISHED",
                OnlineTest.opens_at.is_not(None),
                OnlineTest.opens_at > now,
                OnlineTest.opens_at < now + timedelta(hours=24),
            )
        )
        for test_id, school_id in soon:
            await self._enter(school_id)
            t = await self.db.get(OnlineTest, test_id)
            if not t:
                continue
            window = "1h" if t.opens_at - now <= timedelta(hours=1) else "24h"
            attr = "reminded_1h_at" if window == "1h" else "reminded_24h_at"
            if getattr(t, attr) is not None:
                continue
            setattr(t, attr, now)
            students = await self.svc._enrolled_students(t)
            await self.svc._notify(
                t.school_id, [x.user_id for x in students], title="Test starting soon",
                body=f"{t.title} opens in about {'1 hour' if window == '1h' else 'a day'}.",
                category="ONLINE_TEST_REMINDER", link="/student/online-tests",
                key_prefix=f"OT_REMIND_{window}", entity_id=t.id,
            )
            reminders += 1
        await self.db.flush()
        return {
            "attempts_finalised": finalised, "tests_closed": closed,
            "results_released": released, "reminders": reminders,
        }
