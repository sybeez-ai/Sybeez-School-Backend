"""Voice reading buddy (Classes 1-5). The child reads aloud to Gemini Live (voice only); Core owns
passages, caps, scoring, stars/streaks, retention and audit. Student UI says "AI teacher"."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    ClassRoom,
    Enrollment,
    Grade,
    Parent,
    ReadingPassage,
    ReadingProgress,
    ReadingSession,
    Student,
    StudentParent,
)
from src.modules.access.audit import write_scoped_audit
from src.modules.access.scope import assert_teacher_student
from src.modules.reading_buddy import ai_client, scoring
from src.modules.reading_buddy.schemas import (
    CapsOut,
    CompleteRequest,
    MeOut,
    PassageCreate,
    PassageOut,
    ProgressOut,
    SessionResult,
    SessionSummary,
    StartOut,
    StudentSummary,
)
from src.shared.enums import UserRole

TZ = ZoneInfo("Asia/Kolkata")
MAX_SESSIONS_PER_DAY = 3
MAX_SESSION_SECONDS = 6 * 60
MAX_DAILY_SECONDS = 15 * 60
OPEN_SESSION_GRACE = timedelta(minutes=12)
RETENTION_DAYS = 90
RETENTION_NOTE = (
    "We keep only what you read aloud while practising, to help you improve. "
    f"Transcripts are deleted after {RETENTION_DAYS} days. Stars and scores stay. "
    "Your teacher and parents can see your reading progress."
)
_TAGS = re.compile(r"<[^>]*>")
STAFF = {UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN, UserRole.TEACHER}


def local_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(TZ).date()


def local_midnight_utc(now: datetime | None = None) -> datetime:
    d = local_today(now)
    return datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc)


def progress_out(p: ReadingProgress | None) -> ProgressOut:
    if p is None:
        return ProgressOut()
    return ProgressOut.model_validate({k: getattr(p, k) for k in ProgressOut.model_fields})


class ReadingBuddyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── student context ─────────────────────────────────────────────────────
    async def _student(self, user: CurrentUser, school_id: UUID) -> Student:
        if user.role != UserRole.STUDENT:
            raise ForbiddenError("The reading buddy is for students")
        row = (
            (await self.db.execute(select(Student).where(Student.user_id == user.id, Student.school_id == school_id)))
            .scalars()
            .first()
        )
        if row is None:
            raise NotFoundError("Student profile not found")
        return row

    async def _class_level(self, student: Student) -> int | None:
        level = (
            await self.db.execute(
                select(Grade.level)
                .join(ClassRoom, ClassRoom.grade_id == Grade.id)
                .join(Enrollment, Enrollment.class_id == ClassRoom.id)
                .where(Enrollment.student_id == student.id, Enrollment.status == "ACTIVE")
                .order_by(Enrollment.enrolled_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return int(level) if level is not None else None

    async def _eligible_level(self, student: Student) -> int:
        level = await self._class_level(student)
        if level is None or not 1 <= level <= 5:
            raise ForbiddenError("The reading buddy is for Classes 1 to 5")
        return level

    async def _progress(self, student: Student) -> ReadingProgress | None:
        return (
            await self.db.execute(select(ReadingProgress).where(ReadingProgress.student_id == student.id))
        ).scalar_one_or_none()

    async def _purge_expired_transcripts(self, student: Student) -> None:
        await self.db.execute(
            update(ReadingSession)
            .where(
                ReadingSession.student_id == student.id,
                ReadingSession.retention_until < local_today(),
                ReadingSession.transcript.is_not(None),
            )
            .values(transcript=None)
        )

    async def _usage_today(self, student: Student) -> tuple[int, int]:
        start = local_midnight_utc()
        n, secs = (
            await self.db.execute(
                select(func.count(), func.coalesce(func.sum(ReadingSession.duration_seconds), 0)).where(
                    ReadingSession.student_id == student.id,
                    ReadingSession.started_at >= start,
                    ReadingSession.status != "ABANDONED",
                )
            )
        ).one()
        return int(n), int(secs)

    def _caps(self, sessions_today: int, seconds_today: int) -> CapsOut:
        return CapsOut(
            max_sessions_per_day=MAX_SESSIONS_PER_DAY,
            max_session_minutes=MAX_SESSION_SECONDS // 60,
            max_daily_minutes=MAX_DAILY_SECONDS // 60,
            sessions_today=sessions_today,
            sessions_left_today=max(0, MAX_SESSIONS_PER_DAY - sessions_today),
            minutes_left_today=max(0, (MAX_DAILY_SECONDS - seconds_today) // 60),
            retention_days=RETENTION_DAYS,
            retention_note=RETENTION_NOTE,
        )

    async def me(self, user: CurrentUser, school_id: UUID) -> MeOut:
        student = await self._student(user, school_id)
        level = await self._class_level(student)
        await self._purge_expired_transcripts(student)
        n, secs = await self._usage_today(student)
        return MeOut(
            eligible=level is not None and 1 <= level <= 5,
            class_level=level,
            progress=progress_out(await self._progress(student)),
            caps=self._caps(n, secs),
        )

    # ── passages ────────────────────────────────────────────────────────────
    async def list_passages(self, user: CurrentUser, school_id: UUID, level: int | None) -> list[ReadingPassage]:
        stmt = select(ReadingPassage).where(
            ReadingPassage.is_active.is_(True),
            or_(ReadingPassage.school_id.is_(None), ReadingPassage.school_id == school_id),
        )
        if user.role == UserRole.STUDENT:
            own = await self._eligible_level(await self._student(user, school_id))
            stmt = stmt.where(ReadingPassage.class_level <= own)
            if level is not None:
                stmt = stmt.where(ReadingPassage.class_level == min(max(level, 1), own))
            else:
                stmt = stmt.where(ReadingPassage.class_level == own)
        elif user.role in STAFF:
            if level is not None:
                stmt = stmt.where(ReadingPassage.class_level == level)
        else:
            raise ForbiddenError("Not available")
        stmt = stmt.order_by(ReadingPassage.class_level, ReadingPassage.source.desc(), ReadingPassage.title)
        return list((await self.db.execute(stmt)).scalars())

    async def create_passage(
        self, user: CurrentUser, school_id: UUID, data: PassageCreate, ip: str | None
    ) -> ReadingPassage:
        if user.role not in STAFF:
            raise ForbiddenError("Only teachers and school admins can add passages")
        body = " ".join(_TAGS.sub(" ", data.body).split())
        wc = len(body.split())
        if wc < 8 or wc > 160:
            raise AppError("A passage needs 8 to 160 words", code="invalid_passage", status_code=422)
        row = ReadingPassage(
            school_id=school_id,
            class_level=data.class_level,
            title=_TAGS.sub("", data.title).strip(),
            body=body,
            word_count=wc,
            source="SCHOOL",
            created_by=user.id,
        )
        self.db.add(row)
        await self.db.flush()
        await write_scoped_audit(
            self.db,
            action="reading.passage_created",
            resource_type="reading_passage",
            resource_id=str(row.id),
            message="Reading passage added",
            details={"class_level": data.class_level, "words": wc, "ip": ip},
        )
        return row

    async def set_passage_active(
        self, user: CurrentUser, school_id: UUID, passage_id: UUID, active: bool
    ) -> ReadingPassage:
        if user.role not in STAFF:
            raise ForbiddenError("Staff only")
        row = (
            await self.db.execute(
                select(ReadingPassage).where(ReadingPassage.id == passage_id, ReadingPassage.school_id == school_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Passage not found")
        if user.role == UserRole.TEACHER and row.created_by != user.id:
            raise ForbiddenError("Teachers can only change their own passages")
        row.is_active = active
        await self.db.flush()
        await write_scoped_audit(
            self.db,
            action="reading.passage_toggled",
            resource_type="reading_passage",
            resource_id=str(row.id),
            message="Passage toggled",
            details={"active": active},
        )
        return row

    # ── sessions ────────────────────────────────────────────────────────────
    async def start(self, user: CurrentUser, school_id: UUID, passage_id: UUID, ip: str | None) -> StartOut:
        student = await self._student(user, school_id)
        level = await self._eligible_level(student)
        passage = (
            await self.db.execute(
                select(ReadingPassage).where(
                    ReadingPassage.id == passage_id,
                    ReadingPassage.is_active.is_(True),
                    or_(ReadingPassage.school_id.is_(None), ReadingPassage.school_id == school_id),
                )
            )
        ).scalar_one_or_none()
        if passage is None or passage.class_level > level:
            raise NotFoundError("Passage not found")
        now = datetime.now(timezone.utc)
        # Close forgotten sessions, then allow only one open session.
        open_rows = list(
            (
                await self.db.execute(
                    select(ReadingSession).where(
                        ReadingSession.student_id == student.id, ReadingSession.status == "STARTED"
                    )
                )
            ).scalars()
        )
        for r in open_rows:
            if now - r.started_at > OPEN_SESSION_GRACE:
                r.status, r.ended_at = "EXPIRED", now
            else:
                raise ConflictError("Finish or stop your current reading first", code="session_open")
        n, secs = await self._usage_today(student)
        if n >= MAX_SESSIONS_PER_DAY or secs >= MAX_DAILY_SECONDS:
            raise AppError(
                "That's enough reading for today. Great job! Come back tomorrow.", code="daily_cap", status_code=429
            )
        voice = await ai_client.mint_reading_voice(user, passage=passage.body, class_level=level)
        row = ReadingSession(
            school_id=school_id,
            student_id=student.id,
            passage_id=passage.id,
            status="STARTED",
            started_at=now,
            words_total=passage.word_count,
            retention_until=local_today(now) + timedelta(days=RETENTION_DAYS),
        )
        self.db.add(row)
        await self.db.flush()
        await write_scoped_audit(
            self.db,
            action="reading.session_started",
            resource_type="reading_session",
            resource_id=str(row.id),
            message="Reading session started",
            details={"passage_id": str(passage.id), "class_level": level, "ip": ip},
        )
        return StartOut(
            session_id=row.id,
            passage=PassageOut.model_validate(passage),
            voice={
                "value": voice["value"],
                "ws_url": voice["ws_url"],
                "setup": voice["setup"],
                "max_minutes": int((voice.get("session") or {}).get("max_minutes") or MAX_SESSION_SECONDS // 60),
            },
        )

    async def _own_session(self, student: Student, session_id: UUID) -> ReadingSession:
        row = (
            await self.db.execute(
                select(ReadingSession).where(ReadingSession.id == session_id, ReadingSession.student_id == student.id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Reading session not found")
        return row

    async def complete(
        self, user: CurrentUser, school_id: UUID, session_id: UUID, data: CompleteRequest, ip: str | None
    ) -> SessionResult:
        student = await self._student(user, school_id)
        row = await self._own_session(student, session_id)
        if row.status != "STARTED":
            raise ConflictError("This reading is already finished")
        now = datetime.now(timezone.utc)
        if now - row.started_at > OPEN_SESSION_GRACE:
            row.status, row.ended_at = "EXPIRED", now
            await self.db.flush()
            raise ConflictError("This reading took too long and was closed. Start again!", code="session_expired")
        passage = await self.db.get(ReadingPassage, row.passage_id)
        if passage is None:
            raise NotFoundError("Passage not found")
        duration = min(data.duration_seconds, MAX_SESSION_SECONDS + 30, int((now - row.started_at).total_seconds()) + 5)
        transcript = scoring.clean_transcript(data.transcript)
        res = scoring.analyze(passage.body, transcript, duration)
        row.status, row.ended_at, row.duration_seconds = "COMPLETED", now, duration
        row.transcript = transcript or None
        row.words_total, row.words_correct = res["words_total"], res["words_correct"]
        row.words_misread, row.words_skipped = res["words_misread"], res["words_skipped"]
        row.accuracy_pct, row.wcpm, row.stars = res["accuracy_pct"], res["wcpm"], res["stars"]
        row.word_results = res["word_results"]
        prog = await self._progress(student)
        if prog is None:
            prog = ReadingProgress(
                school_id=school_id,
                student_id=student.id,
                stars_total=0,
                current_streak=0,
                longest_streak=0,
                sessions_completed=0,
                passages_completed=0,
            )
            self.db.add(prog)
        if res["stars"] >= 1:
            today = local_today(now)
            prog.current_streak = scoring.streak_after(prog.last_read_on, today, prog.current_streak)
            prog.longest_streak = max(prog.longest_streak, prog.current_streak)
            prog.last_read_on = today
        prog.stars_total += res["stars"]
        prog.sessions_completed += 1
        if res["completion"] >= 0.9:
            prog.passages_completed += 1
        prog.best_accuracy_pct = max(prog.best_accuracy_pct or 0.0, res["accuracy_pct"])
        await self._purge_expired_transcripts(student)
        await self.db.flush()
        await write_scoped_audit(
            self.db,
            action="reading.session_completed",
            resource_type="reading_session",
            resource_id=str(row.id),
            message="Reading session completed",
            details={"stars": res["stars"], "accuracy_pct": res["accuracy_pct"], "seconds": duration, "ip": ip},
        )
        return SessionResult(
            id=row.id,
            status=row.status,
            stars=row.stars,
            words_total=row.words_total,
            words_correct=row.words_correct,
            words_misread=row.words_misread,
            words_skipped=row.words_skipped,
            accuracy_pct=row.accuracy_pct,
            wcpm=row.wcpm,
            word_results=res["word_results"],
            message=scoring.encouragement(res["stars"], res["completion"]),
            progress=progress_out(prog),
        )

    async def abandon(self, user: CurrentUser, school_id: UUID, session_id: UUID) -> None:
        student = await self._student(user, school_id)
        row = await self._own_session(student, session_id)
        if row.status == "STARTED":
            row.status, row.ended_at = "ABANDONED", datetime.now(timezone.utc)
            await self.db.flush()
            await write_scoped_audit(
                self.db,
                action="reading.session_abandoned",
                resource_type="reading_session",
                resource_id=str(row.id),
                message="Reading session stopped",
            )

    # ── teacher / parent summary ────────────────────────────────────────────
    async def student_summary(self, user: CurrentUser, school_id: UUID, student_id: UUID) -> StudentSummary:
        if user.role == UserRole.TEACHER:
            await assert_teacher_student(self.db, user.id, school_id, student_id)
        elif user.role == UserRole.PARENT:
            linked = (
                await self.db.execute(
                    select(StudentParent.id)
                    .join(Parent, Parent.id == StudentParent.parent_id)
                    .where(
                        Parent.user_id == user.id,
                        StudentParent.student_id == student_id,
                        StudentParent.school_id == school_id,
                    )
                )
            ).first()
            if linked is None:
                raise ForbiddenError("This is not your child")
        elif user.role not in STAFF:
            raise ForbiddenError("Not available")
        student = (
            await self.db.execute(select(Student).where(Student.id == student_id, Student.school_id == school_id))
        ).scalar_one_or_none()
        if student is None:
            raise NotFoundError("Student not found")
        prog = await self._progress(student)
        rows = list(
            (
                await self.db.execute(
                    select(ReadingSession, ReadingPassage.title)
                    .join(ReadingPassage, ReadingPassage.id == ReadingSession.passage_id)
                    .where(ReadingSession.student_id == student_id, ReadingSession.status == "COMPLETED")
                    .order_by(ReadingSession.started_at.desc())
                    .limit(10)
                )
            ).all()
        )
        missed: Counter[str] = Counter()
        for s, _t in rows[:5]:
            for w in s.word_results or []:
                if w.get("status") in {"misread", "skipped"}:
                    missed[re.sub(r"[^\w']", "", str(w.get("word", "")).lower())] += 1
        await write_scoped_audit(
            self.db,
            action="reading.summary_viewed",
            resource_type="student",
            resource_id=str(student_id),
            message="Reading summary viewed",
        )
        return StudentSummary(
            student_id=student_id,
            progress=progress_out(prog),
            recent_sessions=[
                SessionSummary(
                    id=s.id,
                    passage_title=t,
                    started_at=s.started_at,
                    duration_seconds=s.duration_seconds,
                    stars=s.stars,
                    accuracy_pct=s.accuracy_pct,
                    words_total=s.words_total,
                    words_misread=s.words_misread,
                    words_skipped=s.words_skipped,
                )
                for s, t in rows
            ],
            words_to_practise=[w for w, _ in missed.most_common(8) if w],
            note="Transcripts are never shown here. Scores come from speech recognition, so they are a guide, not a grade.",
        )
