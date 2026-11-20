"""Exam prep: mock tests from a class's released MCQ questions, plus weak-topic and readiness tracking."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from math import ceil
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.db.models import (
    Enrollment,
    ExamMockAttempt,
    OnlineTest,
    OnlineTestQuestion,
    OnlineTestQuestionKey,
    Parent,
    PracticeAttempt,
    Student,
    StudentParent,
    Subject,
    Syllabus,
    SyllabusTopic,
)
from src.db.models.class_ import ClassRoom
from src.modules.exam_prep import readiness as R
from src.modules.learning_paths.service import Attempt
from src.modules.online_tests.service import service_scope
from src.shared.enums import EnrollmentStatus, UserRole

GRACE = timedelta(seconds=60)
MIN_Q, MAX_Q = 5, 30


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ExamPrepService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── access ────────────────────────────────────────────────────────────
    @staticmethod
    def _sid(user: CurrentUser) -> UUID:
        if user.school_id is None:
            raise ForbiddenError("School context required")
        return user.school_id

    async def _me(self, user: CurrentUser) -> Student:
        s = (
            await self.db.execute(select(Student).where(Student.school_id == self._sid(user), Student.user_id == user.id))
        ).scalar_one_or_none()
        if s is None:
            raise ForbiddenError("Student profile required")
        return s

    async def _child(self, user: CurrentUser, student_id: UUID) -> Student:
        """A parent's own child (or any student for school admins). Teachers are not allowed."""
        sid = self._sid(user)
        if user.role == UserRole.PARENT:
            linked = (
                await self.db.execute(
                    select(StudentParent.student_id)
                    .join(Parent, Parent.id == StudentParent.parent_id)
                    .where(Parent.user_id == user.id, StudentParent.student_id == student_id)
                )
            ).first()
            if linked is None:
                raise NotFoundError("Student not found")
        elif user.role not in (UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN):
            raise ForbiddenError("Not allowed")
        s = (await self.db.execute(select(Student).where(Student.id == student_id, Student.school_id == sid))).scalar_one_or_none()
        if s is None:
            raise NotFoundError("Student not found")
        return s

    async def _class_ids(self, student: Student) -> list[UUID]:
        return list(
            (
                await self.db.execute(
                    select(Enrollment.class_id).where(
                        Enrollment.student_id == student.id, Enrollment.status == EnrollmentStatus.ACTIVE.value
                    )
                )
            ).scalars()
        )

    # ── question pool (closed tests whose results were released: the student has already seen them) ──
    async def _pool(self, sid: UUID, class_ids: list[UUID], subject_id: UUID | None = None) -> list[dict]:
        if not class_ids:
            return []
        async with service_scope(self.db, sid):
            stmt = (
                select(OnlineTestQuestion, OnlineTest, SyllabusTopic.name)
                .join(OnlineTest, OnlineTest.id == OnlineTestQuestion.test_id)
                .outerjoin(SyllabusTopic, SyllabusTopic.id == OnlineTest.syllabus_topic_id)
                .where(
                    OnlineTest.school_id == sid,
                    OnlineTest.class_id.in_(class_ids),
                    OnlineTest.status == "CLOSED",
                    OnlineTest.results_released_at.is_not(None),
                )
            )
            if subject_id:
                stmt = stmt.where(OnlineTest.subject_id == subject_id)
            rows = (await self.db.execute(stmt)).all()
        return [
            {
                "id": str(q.id),
                "prompt": q.prompt,
                "options": [{"id": o["id"], "text": o["text"]} for o in q.options],
                "points": int(q.points or 1),
                "topic": (topic or t.title or "General").strip(),
                "subject_id": str(t.subject_id),
            }
            for q, t, topic in rows
        ]

    async def available_subjects(self, user: CurrentUser) -> list[dict]:
        student = await self._me(user)
        pool = await self._pool(self._sid(user), await self._class_ids(student))
        counts: dict[str, int] = {}
        for q in pool:
            counts[q["subject_id"]] = counts.get(q["subject_id"], 0) + 1
        if not counts:
            return []
        names = dict((await self.db.execute(select(Subject.id, Subject.name).where(Subject.id.in_([UUID(k) for k in counts])))).all())
        return sorted(
            [{"subject_id": k, "subject_name": names.get(UUID(k)), "questions": v} for k, v in counts.items()],
            key=lambda x: x["subject_name"] or "",
        )

    # ── mock tests ───────────────────────────────────────────────────────
    async def _now(self) -> datetime:
        return (await self.db.execute(select(func.clock_timestamp()))).scalar_one()

    @staticmethod
    def _student_view(row: ExamMockAttempt, now: datetime | None = None) -> dict:
        submitted = row.status == "SUBMITTED"
        out = {
            "id": str(row.id),
            "subject_id": str(row.subject_id) if row.subject_id else None,
            "status": row.status,
            "duration_minutes": row.duration_minutes,
            "started_at": row.started_at,
            "deadline_at": row.deadline_at,
            "submitted_at": row.submitted_at,
            "submit_reason": row.submit_reason,
            "question_count": len(row.questions or []),
            "score": row.score,
            "max_score": row.max_score,
            "percentage": row.percentage,
            "topic_results": row.topic_results,
            "questions": [
                {"id": q["id"], "prompt": q["prompt"], "options": q["options"], "points": q["points"]}
                for q in (row.questions or [])
            ],
            "answers": row.answers or {},
            "review": row.review if submitted else None,
        }
        if now is not None:
            out["seconds_left"] = max(0, int((_utc(row.deadline_at) - _utc(now)).total_seconds())) if not submitted else 0
        return out

    async def start_mock(self, user: CurrentUser, subject_id: UUID, count: int, minutes: int | None) -> dict:
        if not MIN_Q <= count <= MAX_Q:
            raise ValidationError(f"Choose between {MIN_Q} and {MAX_Q} questions")
        student = await self._me(user)
        sid = self._sid(user)
        now = await self._now()
        open_row = (
            await self.db.execute(
                select(ExamMockAttempt).where(ExamMockAttempt.student_id == student.id, ExamMockAttempt.status == "IN_PROGRESS")
            )
        ).scalars().first()
        if open_row is not None:
            if _utc(open_row.deadline_at) + GRACE > _utc(now):
                return self._student_view(open_row, now)  # resume the running mock
            await self._grade(sid, open_row, {}, now, "TIMED_OUT")  # an abandoned mock is scored with what it had
        pool = await self._pool(sid, await self._class_ids(student), subject_id)
        if len(pool) < MIN_Q:
            raise ConflictError(
                "Your teacher has not released enough test questions for this subject yet", code="not_enough_questions"
            )
        picked = random.sample(pool, min(count, len(pool)))
        mins = minutes if minutes else max(5, ceil(len(picked) * 1.0))
        if not 5 <= mins <= 120:
            raise ValidationError("Time must be between 5 and 120 minutes")
        row = ExamMockAttempt(
            school_id=sid, student_id=student.id, subject_id=subject_id, status="IN_PROGRESS", duration_minutes=mins,
            started_at=now, deadline_at=now + timedelta(minutes=mins),
            questions=[{k: q[k] for k in ("id", "prompt", "options", "points", "topic")} for q in picked], answers={},
        )
        self.db.add(row)
        await self.db.flush()
        return self._student_view(row, now)

    async def _own_attempt(self, user: CurrentUser, attempt_id: UUID) -> ExamMockAttempt:
        student = await self._me(user)
        row = (
            await self.db.execute(
                select(ExamMockAttempt).where(ExamMockAttempt.id == attempt_id, ExamMockAttempt.student_id == student.id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Mock test not found")
        return row

    async def get_mock(self, user: CurrentUser, attempt_id: UUID) -> dict:
        row = await self._own_attempt(user, attempt_id)
        return self._student_view(row, await self._now())

    async def save_answers(self, user: CurrentUser, attempt_id: UUID, answers: dict[str, str]) -> dict:
        row = await self._own_attempt(user, attempt_id)
        now = await self._now()
        if row.status != "IN_PROGRESS":
            raise ConflictError("This mock test is already finished", code="already_submitted")
        if _utc(now) > _utc(row.deadline_at):
            raise ConflictError("Time is up", code="time_up")
        row.answers = self._clean(row, {**(row.answers or {}), **answers})
        await self.db.flush()
        return {"saved": len(row.answers)}

    @staticmethod
    def _clean(row: ExamMockAttempt, answers: dict[str, str]) -> dict[str, str]:
        valid = {q["id"]: {o["id"] for o in q["options"]} for q in row.questions or []}
        return {qid: oid for qid, oid in answers.items() if qid in valid and oid in valid[qid]}

    async def submit(self, user: CurrentUser, attempt_id: UUID, answers: dict[str, str]) -> dict:
        row = await self._own_attempt(user, attempt_id)
        now = await self._now()
        if row.status == "SUBMITTED":
            return self._student_view(row, now)
        late = _utc(now) > _utc(row.deadline_at) + GRACE
        merged = dict(row.answers or {})
        if not late:
            merged.update(answers)
        await self._grade(self._sid(user), row, merged, now, "TIMED_OUT" if _utc(now) > _utc(row.deadline_at) else "MANUAL")
        return self._student_view(row, now)

    async def _grade(self, sid: UUID, row: ExamMockAttempt, answers: dict[str, str], now: datetime, reason: str) -> None:
        answers = self._clean(row, {**(row.answers or {}), **answers})
        qids = [UUID(q["id"]) for q in row.questions]
        async with service_scope(self.db, sid):  # keys are hidden from students by RLS
            keys = {
                k.question_id: k
                for k in (await self.db.execute(select(OnlineTestQuestionKey).where(OnlineTestQuestionKey.question_id.in_(qids)))).scalars()
            }
        score = max_score = 0.0
        topics: dict[str, list[float]] = {}
        review = []
        for q in row.questions:
            key = keys.get(UUID(q["id"]))
            correct_id = key.correct_option_id if key else None
            chosen = answers.get(q["id"])
            ok = bool(chosen and correct_id and chosen == correct_id)
            pts = float(q["points"])
            max_score += pts
            score += pts if ok else 0.0
            t = topics.setdefault(q["topic"], [0.0, 0.0])
            t[1] += 1
            t[0] += 1 if ok else 0
            review.append({"id": q["id"], "selected": chosen, "correct_option_id": correct_id, "correct": ok,
                           "explanation": key.explanation if key else None})
        row.answers = answers
        row.review = review
        row.score = score
        row.max_score = max_score
        row.percentage = round(100 * score / max_score, 1) if max_score else 0.0
        row.topic_results = [
            {"topic": t, "correct": int(c), "total": int(n), "pct": round(100 * c / n, 1)} for t, (c, n) in sorted(topics.items())
        ]
        row.status = "SUBMITTED"
        row.submitted_at = now
        row.submit_reason = reason
        await self.db.flush()

    async def history(self, user: CurrentUser, student_id: UUID | None = None, limit: int = 20) -> list[dict]:
        student = await self._me(user) if student_id is None else await self._child(user, student_id)
        rows = (
            await self.db.execute(
                select(ExamMockAttempt)
                .where(ExamMockAttempt.student_id == student.id, ExamMockAttempt.status == "SUBMITTED")
                .order_by(ExamMockAttempt.submitted_at.desc())
                .limit(min(max(limit, 1), 50))
            )
        ).scalars().all()
        names = {}
        ids = {r.subject_id for r in rows if r.subject_id}
        if ids:
            names = dict((await self.db.execute(select(Subject.id, Subject.name).where(Subject.id.in_(ids)))).all())
        return [
            {"id": str(r.id), "subject_id": str(r.subject_id) if r.subject_id else None,
             "subject_name": names.get(r.subject_id), "submitted_at": r.submitted_at, "percentage": r.percentage,
             "score": r.score, "max_score": r.max_score, "duration_minutes": r.duration_minutes,
             "submit_reason": r.submit_reason, "topic_results": r.topic_results}
            for r in rows
        ]

    # ── readiness ────────────────────────────────────────────────────────
    async def readiness(self, user: CurrentUser, student_id: UUID | None = None) -> dict:
        student = await self._me(user) if student_id is None else await self._child(user, student_id)
        sid = self._sid(user)
        now = datetime.now(timezone.utc)
        mocks = (
            await self.db.execute(
                select(ExamMockAttempt).where(ExamMockAttempt.student_id == student.id, ExamMockAttempt.status == "SUBMITTED")
            )
        ).scalars().all()
        practice: list[PracticeAttempt] = []
        async with service_scope(self.db, sid):  # practice rows are private to the student's own login
            practice = list(
                (
                    await self.db.execute(
                        select(PracticeAttempt)
                        .where(
                            PracticeAttempt.student_user_id == student.user_id,
                            PracticeAttempt.completed_at.is_not(None), PracticeAttempt.score_pct.is_not(None),
                            PracticeAttempt.topic.is_not(None),
                        )
                        .order_by(PracticeAttempt.completed_at.desc())
                        .limit(300)
                    )
                ).scalars()
            )
        by_subject: dict[UUID | None, list[Attempt]] = {}
        mock_scores: dict[UUID | None, list[tuple[datetime, float]]] = {}
        for p in practice:
            if (p.topic or "").strip():
                by_subject.setdefault(p.subject_id, []).append(Attempt(p.subject_id, p.topic, float(p.score_pct or 0), p.completed_at))
        for m in mocks:
            at = m.submitted_at or m.started_at
            mock_scores.setdefault(m.subject_id, []).append((at, float(m.percentage or 0)))
            for tr in m.topic_results or []:
                by_subject.setdefault(m.subject_id, []).append(Attempt(m.subject_id, tr["topic"], float(tr["pct"]), at))
        subject_ids = {s for s in set(by_subject) | set(mock_scores) if s}
        names = dict((await self.db.execute(select(Subject.id, Subject.name).where(Subject.id.in_(subject_ids)))).all()) if subject_ids else {}
        grade_id = None
        class_ids = await self._class_ids(student)
        if class_ids:
            grade_id = (await self.db.execute(select(ClassRoom.grade_id).where(ClassRoom.id == class_ids[0]))).scalar_one_or_none()
        subjects = []
        for sub in sorted(subject_ids, key=lambda s: names.get(s) or ""):
            syl_topics: list[str] = []
            if grade_id:
                syl_topics = list(
                    (
                        await self.db.execute(
                            select(SyllabusTopic.name)
                            .join(Syllabus, Syllabus.id == SyllabusTopic.syllabus_id)
                            .where(Syllabus.school_id == sid, Syllabus.grade_id == grade_id, Syllabus.subject_id == sub)
                        )
                    ).scalars()
                )
            subjects.append(R.subject_readiness(sub, names.get(sub), by_subject.get(sub, []), syl_topics, mock_scores.get(sub, []), now))
        return {
            "student_id": str(student.id),
            "has_data": bool(subjects),
            "subjects": subjects,
            "method": R.METHOD,
            "message": None if subjects else "Take a practice quiz or a mock test and your readiness will appear here.",
        }
