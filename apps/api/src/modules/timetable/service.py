from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.modules.access.scope import restrict_classes
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Parent,
    Room,
    Student,
    StudentParent,
    Subject,
    Teacher,
    TimetableEntry,
    TimetablePeriod,
    TimetableSettings,
    User,
)
from src.modules.timetable.schemas import (
    ChildBriefOut,
    GridCellOut,
    PeriodCreate,
    PeriodOut,
    PeriodReorderRequest,
    PeriodUpdate,
    RoomCreate,
    RoomOut,
    RoomUpdate,
    TeacherPeriodOut,
    TimetableEntryCreate,
    TimetableEntryOut,
    TimetableEntryUpdate,
    TimetableGridOut,
    TimetableSettingsOut,
    TimetableSettingsUpdate,
    TodayItemOut,
    TodayScheduleOut,
)
from src.shared.enums import EnrollmentStatus, PeriodKind, UserRole

DEFAULT_WORKING_DAYS = "0,1,2,3,4"

DEFAULT_PERIODS: list[tuple[str, str, time, time]] = [
    ("Period 1", PeriodKind.LESSON.value, time(9, 0), time(9, 45)),
    ("Period 2", PeriodKind.LESSON.value, time(9, 45), time(10, 30)),
    ("Break", PeriodKind.BREAK.value, time(10, 30), time(10, 45)),
    ("Period 3", PeriodKind.LESSON.value, time(10, 45), time(11, 30)),
    ("Period 4", PeriodKind.LESSON.value, time(11, 30), time(12, 15)),
    ("Lunch", PeriodKind.LUNCH.value, time(12, 15), time(13, 0)),
    ("Period 5", PeriodKind.LESSON.value, time(13, 0), time(13, 45)),
    ("Period 6", PeriodKind.LESSON.value, time(13, 45), time(14, 30)),
]


def _weekday_monday0(d: date) -> int:
    return d.weekday()


def _slot_status(starts: time, ends: time, now: time) -> str:
    if now < starts:
        return "upcoming"
    if starts <= now <= ends:
        return "now"
    return "done"


def _parse_working_days(raw: str | None) -> list[int]:
    if not raw:
        return [0, 1, 2, 3, 4]
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if 0 <= n <= 6:
            out.append(n)
    return sorted(set(out)) or [0, 1, 2, 3, 4]


def _times_overlap(s1: time, e1: time, s2: time, e2: time) -> bool:
    return s1 < e2 and s2 < e1


class TimetableService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── helpers ──────────────────────────────────────────────

    async def _user_name(self, user_id: UUID | None) -> str | None:
        if not user_id:
            return None
        user = await self.db.get(User, user_id)
        return user.full_name if user else None

    async def _teacher_name(self, teacher_id: UUID | None) -> str | None:
        if not teacher_id:
            return None
        teacher = await self.db.get(Teacher, teacher_id)
        if not teacher:
            return None
        return await self._user_name(teacher.user_id)

    async def _student_name(self, student_id: UUID) -> str | None:
        student = await self.db.get(Student, student_id)
        if not student:
            return None
        return await self._user_name(student.user_id)

    async def _class_name(self, class_id: UUID | None) -> str | None:
        if not class_id:
            return None
        classroom = await self.db.get(ClassRoom, class_id)
        return classroom.name if classroom else None

    async def _subject_name(self, subject_id: UUID | None) -> str | None:
        if not subject_id:
            return None
        subject = await self.db.get(Subject, subject_id)
        return subject.name if subject else None

    async def _to_entry_out(self, row: TimetableEntry) -> TimetableEntryOut:
        period = await self.db.get(TimetablePeriod, row.period_id) if row.period_id else None
        room = await self.db.get(Room, row.room_id) if row.room_id else None
        room_label = room.name if room else row.room
        return TimetableEntryOut(
            id=row.id,
            school_id=row.school_id,
            academic_year_id=row.academic_year_id,
            class_id=row.class_id,
            class_name=await self._class_name(row.class_id),
            subject_id=row.subject_id,
            subject_name=await self._subject_name(row.subject_id),
            teacher_id=row.teacher_id,
            teacher_name=await self._teacher_name(row.teacher_id),
            period_id=row.period_id,
            period_name=period.name if period else None,
            period_kind=period.kind if period else PeriodKind.LESSON.value,
            day_of_week=row.day_of_week,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            room=room_label,
            room_id=row.room_id,
            room_name=room.name if room else None,
            created_at=row.created_at,
        )

    async def teacher_for_user(self, *, school_id: UUID, user_id: UUID) -> Teacher:
        result = await self.db.execute(
            select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
        )
        teacher = result.scalar_one_or_none()
        if teacher is None:
            raise ForbiddenError("Teacher profile required")
        return teacher

    async def student_for_user(self, *, school_id: UUID, user_id: UUID) -> Student:
        result = await self.db.execute(
            select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
        )
        student = result.scalar_one_or_none()
        if student is None:
            raise ForbiddenError("Student profile required")
        return student

    async def parent_for_user(self, *, school_id: UUID, user_id: UUID) -> Parent:
        result = await self.db.execute(
            select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
        )
        parent = result.scalar_one_or_none()
        if parent is None:
            raise ForbiddenError("Parent profile required")
        return parent

    async def assert_parent_of(
        self, *, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> None:
        parent = await self.parent_for_user(school_id=school_id, user_id=user_id)
        link = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent.id,
                    StudentParent.student_id == student_id,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            raise ForbiddenError("Not linked to this student")

    async def _active_enrollment(
        self, school_id: UUID, student_id: UUID
    ) -> Enrollment | None:
        return (
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

    # ── settings ─────────────────────────────────────────────

    async def get_or_create_settings(self, school_id: UUID) -> TimetableSettings:
        row = (
            await self.db.execute(
                select(TimetableSettings).where(TimetableSettings.school_id == school_id)
            )
        ).scalar_one_or_none()
        if row:
            return row
        row = TimetableSettings(school_id=school_id, working_days=DEFAULT_WORKING_DAYS)
        self.db.add(row)
        await self.db.flush()
        return row

    async def get_settings(self, school_id: UUID) -> TimetableSettingsOut:
        row = await self.get_or_create_settings(school_id)
        return TimetableSettingsOut(working_days=_parse_working_days(row.working_days))

    async def update_settings(
        self, school_id: UUID, data: TimetableSettingsUpdate
    ) -> TimetableSettingsOut:
        row = await self.get_or_create_settings(school_id)
        row.working_days = ",".join(str(d) for d in data.working_days)
        return TimetableSettingsOut(working_days=data.working_days)

    # ── periods ──────────────────────────────────────────────

    async def list_periods(self, school_id: UUID, *, enabled_only: bool = False) -> list[PeriodOut]:
        q = select(TimetablePeriod).where(TimetablePeriod.school_id == school_id)
        if enabled_only:
            q = q.where(TimetablePeriod.is_enabled.is_(True))
        result = await self.db.execute(q.order_by(TimetablePeriod.sort_order, TimetablePeriod.starts_at))
        rows = list(result.scalars().all())
        if not rows:
            rows = await self._seed_default_periods(school_id)
        return [PeriodOut.model_validate(r) for r in rows]

    async def _seed_default_periods(self, school_id: UUID) -> list[TimetablePeriod]:
        rows: list[TimetablePeriod] = []
        for i, (name, kind, starts, ends) in enumerate(DEFAULT_PERIODS):
            row = TimetablePeriod(
                school_id=school_id,
                name=name,
                kind=kind,
                sort_order=i,
                starts_at=starts,
                ends_at=ends,
                is_enabled=True,
            )
            self.db.add(row)
            rows.append(row)
        await self.db.flush()
        return rows

    async def create_period(self, school_id: UUID, data: PeriodCreate) -> PeriodOut:
        if data.sort_order is None:
            max_order = (
                await self.db.execute(
                    select(TimetablePeriod.sort_order)
                    .where(TimetablePeriod.school_id == school_id)
                    .order_by(TimetablePeriod.sort_order.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            sort_order = (max_order or -1) + 1
        else:
            sort_order = data.sort_order
        row = TimetablePeriod(
            school_id=school_id,
            name=data.name.strip(),
            kind=data.kind.value,
            sort_order=sort_order,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            is_enabled=data.is_enabled,
        )
        self.db.add(row)
        await self.db.flush()
        return PeriodOut.model_validate(row)

    async def update_period(
        self, school_id: UUID, period_id: UUID, data: PeriodUpdate
    ) -> PeriodOut:
        row = await self.db.get(TimetablePeriod, period_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Period not found")
        payload = data.model_dump(exclude_unset=True)
        if "kind" in payload and payload["kind"] is not None:
            payload["kind"] = payload["kind"].value if hasattr(payload["kind"], "value") else payload["kind"]
        if "name" in payload and payload["name"]:
            payload["name"] = payload["name"].strip()
        for k, v in payload.items():
            setattr(row, k, v)
        if row.ends_at <= row.starts_at:
            raise AppError("ends_at must be after starts_at", code="validation_error", status_code=400)
        # Keep linked lesson entries in sync with period times
        if "starts_at" in payload or "ends_at" in payload:
            linked = (
                await self.db.execute(
                    select(TimetableEntry).where(
                        TimetableEntry.school_id == school_id,
                        TimetableEntry.period_id == period_id,
                    )
                )
            ).scalars().all()
            for entry in linked:
                entry.starts_at = row.starts_at
                entry.ends_at = row.ends_at
        return PeriodOut.model_validate(row)

    async def delete_period(self, school_id: UUID, period_id: UUID) -> None:
        row = await self.db.get(TimetablePeriod, period_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Period not found")
        linked = (
            await self.db.execute(
                select(TimetableEntry.id).where(
                    TimetableEntry.school_id == school_id,
                    TimetableEntry.period_id == period_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if linked:
            raise ConflictError(
                "Period has timetable entries. Remove or reassign them first.",
                code="period_in_use",
            )
        await self.db.delete(row)

    async def reorder_periods(
        self, school_id: UUID, data: PeriodReorderRequest
    ) -> list[PeriodOut]:
        for item in data.items:
            row = await self.db.get(TimetablePeriod, item.id)
            if not row or row.school_id != school_id:
                raise NotFoundError("Period not found")
            row.sort_order = item.sort_order
        return await self.list_periods(school_id)

    # ── rooms ────────────────────────────────────────────────

    async def list_rooms(self, school_id: UUID, *, active_only: bool = False) -> list[RoomOut]:
        q = select(Room).where(Room.school_id == school_id)
        if active_only:
            q = q.where(Room.is_active.is_(True))
        result = await self.db.execute(q.order_by(Room.name))
        return [RoomOut.model_validate(r) for r in result.scalars().all()]

    async def create_room(self, school_id: UUID, data: RoomCreate) -> RoomOut:
        row = Room(
            school_id=school_id,
            name=data.name.strip(),
            code=data.code.strip() if data.code else None,
            capacity=data.capacity,
            is_active=data.is_active,
        )
        self.db.add(row)
        await self.db.flush()
        return RoomOut.model_validate(row)

    async def update_room(self, school_id: UUID, room_id: UUID, data: RoomUpdate) -> RoomOut:
        row = await self.db.get(Room, room_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Room not found")
        for k, v in data.model_dump(exclude_unset=True).items():
            if k in ("name", "code") and isinstance(v, str):
                v = v.strip() or None
            setattr(row, k, v)
        return RoomOut.model_validate(row)

    async def delete_room(self, school_id: UUID, room_id: UUID) -> None:
        row = await self.db.get(Room, room_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Room not found")
        linked = (
            await self.db.execute(
                select(TimetableEntry.id).where(
                    TimetableEntry.school_id == school_id,
                    TimetableEntry.room_id == room_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if linked:
            # Soft-clear rather than block MVP deletes
            entries = (
                await self.db.execute(
                    select(TimetableEntry).where(
                        TimetableEntry.school_id == school_id,
                        TimetableEntry.room_id == room_id,
                    )
                )
            ).scalars().all()
            for e in entries:
                e.room_id = None
        await self.db.delete(row)

    # ── validation + conflicts ───────────────────────────────

    async def _resolve_times(
        self, school_id: UUID, period_id: UUID | None, starts_at: time | None, ends_at: time | None
    ) -> tuple[UUID | None, time, time, TimetablePeriod | None]:
        period: TimetablePeriod | None = None
        if period_id:
            period = await self.db.get(TimetablePeriod, period_id)
            if not period or period.school_id != school_id:
                raise NotFoundError("Period not found")
            if period.kind != PeriodKind.LESSON.value:
                raise AppError(
                    "Cannot schedule a lesson on a break/lunch period",
                    code="invalid_period",
                    status_code=400,
                )
            if not period.is_enabled:
                raise AppError("Period is disabled", code="period_disabled", status_code=400)
            return period_id, period.starts_at, period.ends_at, period
        if starts_at is None or ends_at is None:
            raise AppError("starts_at and ends_at required", code="validation_error", status_code=400)
        return None, starts_at, ends_at, None

    async def _assert_refs(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        subject_id: UUID,
        teacher_id: UUID | None,
        academic_year_id: UUID | None,
        room_id: UUID | None,
    ) -> ClassRoom:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Section not found")
        subject = await self.db.get(Subject, subject_id)
        if not subject or subject.school_id != school_id:
            raise NotFoundError("Subject not found")
        if teacher_id:
            teacher = await self.db.get(Teacher, teacher_id)
            if not teacher or teacher.school_id != school_id:
                raise NotFoundError("Teacher not found")
        if academic_year_id:
            year = await self.db.get(AcademicYear, academic_year_id)
            if not year or year.school_id != school_id:
                raise NotFoundError("Academic year not found")
            if classroom.academic_year_id and classroom.academic_year_id != academic_year_id:
                raise AppError(
                    "Section does not belong to this academic year",
                    code="year_mismatch",
                    status_code=400,
                )
        if room_id:
            room = await self.db.get(Room, room_id)
            if not room or room.school_id != school_id:
                raise NotFoundError("Room not found")
        return classroom

    async def _assert_no_conflicts(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        teacher_id: UUID | None,
        room_id: UUID | None,
        day_of_week: int,
        period_id: UUID | None,
        starts_at: time,
        ends_at: time,
        exclude_id: UUID | None = None,
    ) -> None:
        q = select(TimetableEntry).where(
            TimetableEntry.school_id == school_id,
            TimetableEntry.day_of_week == day_of_week,
        )
        if exclude_id:
            q = q.where(TimetableEntry.id != exclude_id)
        rows = list((await self.db.execute(q)).scalars().all())

        for other in rows:
            same_period = period_id and other.period_id and period_id == other.period_id
            overlap = same_period or _times_overlap(starts_at, ends_at, other.starts_at, other.ends_at)
            if not overlap:
                continue
            if other.class_id == class_id:
                raise ConflictError(
                    "Section already has a class in this period",
                    code="section_conflict",
                )
            if teacher_id and other.teacher_id == teacher_id:
                other_class = await self._class_name(other.class_id) or "another class"
                raise ConflictError(
                    f"Teacher is already assigned to {other_class} in this period",
                    code="teacher_conflict",
                )
            if room_id and other.room_id == room_id:
                raise ConflictError(
                    "Room is already booked in this period",
                    code="room_conflict",
                )

    # ── entries CRUD ─────────────────────────────────────────

    async def create_entry(self, school_id: UUID, data: TimetableEntryCreate) -> TimetableEntryOut:
        classroom = await self._assert_refs(
            school_id=school_id,
            class_id=data.class_id,
            subject_id=data.subject_id,
            teacher_id=data.teacher_id,
            academic_year_id=data.academic_year_id,
            room_id=data.room_id,
        )
        period_id, starts_at, ends_at, _ = await self._resolve_times(
            school_id, data.period_id, data.starts_at, data.ends_at
        )
        await self._assert_no_conflicts(
            school_id=school_id,
            class_id=data.class_id,
            teacher_id=data.teacher_id,
            room_id=data.room_id,
            day_of_week=data.day_of_week,
            period_id=period_id,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        year_id = data.academic_year_id or classroom.academic_year_id
        room_label = data.room
        if data.room_id and not room_label:
            room = await self.db.get(Room, data.room_id)
            room_label = room.name if room else None

        row = TimetableEntry(
            school_id=school_id,
            academic_year_id=year_id,
            class_id=data.class_id,
            subject_id=data.subject_id,
            teacher_id=data.teacher_id,
            period_id=period_id,
            day_of_week=data.day_of_week,
            starts_at=starts_at,
            ends_at=ends_at,
            room=room_label,
            room_id=data.room_id,
        )
        self.db.add(row)
        await self.db.flush()
        return await self._to_entry_out(row)

    async def update_entry(
        self, school_id: UUID, entry_id: UUID, data: TimetableEntryUpdate
    ) -> TimetableEntryOut:
        row = await self.db.get(TimetableEntry, entry_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Timetable entry not found")

        payload = data.model_dump(exclude_unset=True)
        subject_id = payload.get("subject_id", row.subject_id)
        teacher_id = payload["teacher_id"] if "teacher_id" in payload else row.teacher_id
        room_id = payload["room_id"] if "room_id" in payload else row.room_id
        day = payload.get("day_of_week", row.day_of_week)

        if "period_id" in payload and payload["period_id"]:
            period_id, starts, ends, _ = await self._resolve_times(
                school_id, payload["period_id"], None, None
            )
        elif "starts_at" in payload or "ends_at" in payload:
            period_id = payload.get("period_id", row.period_id)
            starts = payload.get("starts_at", row.starts_at)
            ends = payload.get("ends_at", row.ends_at)
            if ends <= starts:
                raise AppError("ends_at must be after starts_at", code="validation_error", status_code=400)
        else:
            period_id = row.period_id
            starts = row.starts_at
            ends = row.ends_at

        await self._assert_refs(
            school_id=school_id,
            class_id=row.class_id,
            subject_id=subject_id,
            teacher_id=teacher_id,
            academic_year_id=row.academic_year_id,
            room_id=room_id,
        )
        await self._assert_no_conflicts(
            school_id=school_id,
            class_id=row.class_id,
            teacher_id=teacher_id,
            room_id=room_id,
            day_of_week=day,
            period_id=period_id,
            starts_at=starts,
            ends_at=ends,
            exclude_id=row.id,
        )

        row.subject_id = subject_id
        row.teacher_id = teacher_id
        row.day_of_week = day
        row.period_id = period_id
        row.starts_at = starts
        row.ends_at = ends
        row.room_id = room_id
        if "room" in payload:
            row.room = payload["room"]
        elif room_id:
            room = await self.db.get(Room, room_id)
            row.room = room.name if room else row.room

        return await self._to_entry_out(row)

    async def delete_entry(self, school_id: UUID, entry_id: UUID) -> None:
        row = await self.db.get(TimetableEntry, entry_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Timetable entry not found")
        await self.db.delete(row)

    async def list_entries(
        self,
        *,
        school_id: UUID,
        class_id: UUID | None = None,
        teacher_id: UUID | None = None,
        academic_year_id: UUID | None = None,
        day_of_week: int | None = None,
        user: CurrentUser | None = None,
    ) -> list[TimetableEntryOut]:
        q = select(TimetableEntry).where(TimetableEntry.school_id == school_id)
        if user is not None:
            q = restrict_classes(q, user, school_id, TimetableEntry.class_id)
        if class_id:
            q = q.where(TimetableEntry.class_id == class_id)
        if teacher_id:
            q = q.where(TimetableEntry.teacher_id == teacher_id)
        if academic_year_id:
            q = q.where(TimetableEntry.academic_year_id == academic_year_id)
        if day_of_week is not None:
            q = q.where(TimetableEntry.day_of_week == day_of_week)
        result = await self.db.execute(
            q.order_by(TimetableEntry.day_of_week, TimetableEntry.starts_at)
        )
        return [await self._to_entry_out(r) for r in result.scalars().all()]

    # ── grid ─────────────────────────────────────────────────

    async def grid(self, *, school_id: UUID, class_id: UUID, user: CurrentUser | None = None) -> TimetableGridOut:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Section not found")
        if user is not None and user.role == UserRole.TEACHER:
            from src.modules.access.scope import assert_teacher_class

            await assert_teacher_class(self.db, user.id, school_id, class_id)
        settings = await self.get_settings(school_id)
        periods = await self.list_periods(school_id, enabled_only=True)
        entries = await self.list_entries(school_id=school_id, class_id=class_id)
        by_key = {(e.day_of_week, e.period_id): e for e in entries if e.period_id}

        cells: list[GridCellOut] = []
        for day in settings.working_days:
            for p in periods:
                cells.append(
                    GridCellOut(
                        day_of_week=day,
                        period_id=p.id,
                        period_name=p.name,
                        period_kind=p.kind,
                        starts_at=p.starts_at,
                        ends_at=p.ends_at,
                        entry=by_key.get((day, p.id)),
                    )
                )
        return TimetableGridOut(
            class_id=class_id,
            class_name=classroom.name,
            working_days=settings.working_days,
            periods=periods,
            cells=cells,
        )

    # ── role views ───────────────────────────────────────────

    async def list_for_teacher(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        on_date: date | None = None,
        as_of: time | None = None,
        week: bool = False,
    ) -> list[TeacherPeriodOut] | list[TimetableEntryOut]:
        teacher = await self.teacher_for_user(school_id=school_id, user_id=user_id)
        if week:
            return await self.list_entries(school_id=school_id, teacher_id=teacher.id)

        day = on_date or date.today()
        now_t = as_of or datetime.now().time().replace(second=0, microsecond=0)
        dow = _weekday_monday0(day)
        entries = await self.list_entries(
            school_id=school_id, teacher_id=teacher.id, day_of_week=dow
        )
        out: list[TeacherPeriodOut] = []
        for e in entries:
            out.append(
                TeacherPeriodOut(
                    id=e.id,
                    class_id=e.class_id,
                    class_name=e.class_name or "Class",
                    subject_id=e.subject_id,
                    subject_name=e.subject_name or "Subject",
                    day_of_week=e.day_of_week,
                    starts_at=e.starts_at,
                    ends_at=e.ends_at,
                    room=e.room,
                    period_id=e.period_id,
                    period_name=e.period_name,
                    slot_status=_slot_status(e.starts_at, e.ends_at, now_t),
                )
            )
        return out

    async def list_for_student(
        self, *, school_id: UUID, user_id: UUID
    ) -> list[TimetableEntryOut]:
        student = await self.student_for_user(school_id=school_id, user_id=user_id)
        enr = await self._active_enrollment(school_id, student.id)
        if not enr:
            return []
        return await self.list_entries(school_id=school_id, class_id=enr.class_id)

    async def list_children(self, *, school_id: UUID, user_id: UUID) -> list[ChildBriefOut]:
        parent = await self.parent_for_user(school_id=school_id, user_id=user_id)
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
            enr = await self._active_enrollment(school_id, link.student_id)
            out.append(
                ChildBriefOut(
                    student_id=link.student_id,
                    full_name=await self._student_name(link.student_id) or "Student",
                    class_id=enr.class_id if enr else None,
                    class_name=await self._class_name(enr.class_id) if enr else None,
                )
            )
        return out

    async def list_for_parent_child(
        self, *, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> list[TimetableEntryOut]:
        await self.assert_parent_of(school_id=school_id, user_id=user_id, student_id=student_id)
        enr = await self._active_enrollment(school_id, student_id)
        if not enr:
            return []
        return await self.list_entries(school_id=school_id, class_id=enr.class_id)

    async def today(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        on_date: date | None = None,
        student_id: UUID | None = None,
        as_of: time | None = None,
    ) -> TodayScheduleOut:
        day = on_date or date.today()
        dow = _weekday_monday0(day)
        now_t = as_of or datetime.now().time().replace(second=0, microsecond=0)
        periods = await self.list_periods(school_id, enabled_only=True)

        items: list[TodayItemOut] = []
        sid: UUID | None = None
        sname: str | None = None
        cname: str | None = None

        if role == UserRole.TEACHER.value:
            periods_today = await self.list_for_teacher(
                school_id=school_id, user_id=user_id, on_date=day, as_of=now_t
            )
            assert isinstance(periods_today, list)
            for p in periods_today:
                if not isinstance(p, TeacherPeriodOut):
                    continue
                items.append(
                    TodayItemOut(
                        id=p.id,
                        starts_at=p.starts_at,
                        ends_at=p.ends_at,
                        period_name=p.period_name,
                        period_kind=PeriodKind.LESSON.value,
                        subject_name=p.subject_name,
                        class_name=p.class_name,
                        room=p.room,
                        class_id=p.class_id,
                        subject_id=p.subject_id,
                        slot_status=p.slot_status,
                    )
                )
        elif role == UserRole.STUDENT.value:
            student = await self.student_for_user(school_id=school_id, user_id=user_id)
            sid = student.id
            sname = await self._student_name(student.id)
            enr = await self._active_enrollment(school_id, student.id)
            cname = await self._class_name(enr.class_id) if enr else None
            entries = await self.list_for_student(school_id=school_id, user_id=user_id)
            day_entries = [e for e in entries if e.day_of_week == dow]
            items = await self._today_items_for_section(
                school_id=school_id,
                class_id=enr.class_id if enr else None,
                day_entries=day_entries,
                periods=periods,
                now_t=now_t,
            )
        elif role == UserRole.PARENT.value:
            if not student_id:
                children = await self.list_children(school_id=school_id, user_id=user_id)
                if not children:
                    return TodayScheduleOut(
                        on_date=day.isoformat(),
                        day_of_week=dow,
                        items=[],
                    )
                student_id = children[0].student_id
            await self.assert_parent_of(school_id=school_id, user_id=user_id, student_id=student_id)
            sid = student_id
            sname = await self._student_name(student_id)
            enr = await self._active_enrollment(school_id, student_id)
            cname = await self._class_name(enr.class_id) if enr else None
            entries = await self.list_for_parent_child(
                school_id=school_id, user_id=user_id, student_id=student_id
            )
            day_entries = [e for e in entries if e.day_of_week == dow]
            items = await self._today_items_for_section(
                school_id=school_id,
                class_id=enr.class_id if enr else None,
                day_entries=day_entries,
                periods=periods,
                now_t=now_t,
            )
        else:
            # Admin: empty unless class filter — use periods template only
            for p in periods:
                items.append(
                    TodayItemOut(
                        id=None,
                        starts_at=p.starts_at,
                        ends_at=p.ends_at,
                        period_name=p.name,
                        period_kind=p.kind,
                        slot_status=_slot_status(p.starts_at, p.ends_at, now_t),
                    )
                )

        return TodayScheduleOut(
            on_date=day.isoformat(),
            day_of_week=dow,
            student_id=sid,
            student_name=sname,
            class_name=cname,
            items=items,
        )

    async def _today_items_for_section(
        self,
        *,
        school_id: UUID,
        class_id: UUID | None,
        day_entries: list[TimetableEntryOut],
        periods: list[PeriodOut],
        now_t: time,
    ) -> list[TodayItemOut]:
        by_period = {e.period_id: e for e in day_entries if e.period_id}
        # Also map by time for legacy entries without period_id
        by_time = {(e.starts_at, e.ends_at): e for e in day_entries}

        if periods:
            items: list[TodayItemOut] = []
            for p in periods:
                entry = by_period.get(p.id) or by_time.get((p.starts_at, p.ends_at))
                if p.kind != PeriodKind.LESSON.value:
                    items.append(
                        TodayItemOut(
                            id=None,
                            starts_at=p.starts_at,
                            ends_at=p.ends_at,
                            period_name=p.name,
                            period_kind=p.kind,
                            slot_status=_slot_status(p.starts_at, p.ends_at, now_t),
                        )
                    )
                    continue
                items.append(
                    TodayItemOut(
                        id=entry.id if entry else None,
                        starts_at=p.starts_at,
                        ends_at=p.ends_at,
                        period_name=p.name,
                        period_kind=p.kind,
                        subject_name=entry.subject_name if entry else None,
                        class_name=entry.class_name if entry else None,
                        teacher_name=entry.teacher_name if entry else None,
                        room=entry.room if entry else None,
                        class_id=entry.class_id if entry else class_id,
                        subject_id=entry.subject_id if entry else None,
                        slot_status=_slot_status(p.starts_at, p.ends_at, now_t),
                    )
                )
            return items

        return [
            TodayItemOut(
                id=e.id,
                starts_at=e.starts_at,
                ends_at=e.ends_at,
                period_name=e.period_name,
                period_kind=PeriodKind.LESSON.value,
                subject_name=e.subject_name,
                class_name=e.class_name,
                teacher_name=e.teacher_name,
                room=e.room,
                class_id=e.class_id,
                subject_id=e.subject_id,
                slot_status=_slot_status(e.starts_at, e.ends_at, now_t),
            )
            for e in sorted(day_entries, key=lambda x: x.starts_at)
        ]
