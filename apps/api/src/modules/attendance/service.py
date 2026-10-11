from calendar import monthrange
from datetime import date, datetime, timedelta, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.modules.access.scope import restrict_student_rows
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams
from src.db.models import (
    Attendance,
    AttendanceAuditLog,
    AttendanceDigestLog,
    ClassRoom,
    Enrollment,
    LeaveRequest,
    Parent,
    Student,
    StudentParent,
    Teacher,
    TeacherAssignment,
    TimetableEntry,
    User,
)
from src.modules.attendance.repository import AttendanceRepository, LeaveRequestRepository
from src.modules.attendance.schemas import (
    AlertOut,
    AttendanceMarkRequest,
    AttendanceOut,
    AttendanceSettingsOut,
    AttendanceSettingsUpdate,
    AttendanceSummaryOut,
    AuditLogOut,
    ChildOut,
    ClassLowAttendanceOut,
    ClassRateOut,
    LeaveDecisionRequest,
    LeaveRequestCreate,
    LeaveRequestOut,
    MonthlyStatsOut,
    OverviewOut,
    RosterStudentOut,
    StudentRateOut,
)
from src.modules.notifications.service import create_in_app_notification
from src.modules.notifications.deliveries import queue_outbound_delivery
from src.shared.enums import (
    AttendanceStatus,
    EnrollmentStatus,
    LeaveRequestStatus,
    NotificationChannel,
    NotificationType,
    UserRole,
)


def _rate(present_like: int, total: int) -> float:
    if total <= 0:
        return 100.0
    return round((present_like / total) * 100, 1)


def _present_like(status: str) -> bool:
    return status in {
        AttendanceStatus.PRESENT.value,
        AttendanceStatus.LATE.value,
        AttendanceStatus.EXCUSED.value,
        AttendanceStatus.HALF_DAY.value,
    }


class AttendanceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AttendanceRepository(db)
        self.leave_repo = LeaveRequestRepository(db)

    async def _user_name(self, user_id: UUID | None) -> str | None:
        if not user_id:
            return None
        user = await self.db.get(User, user_id)
        return user.full_name if user else None

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

    async def _active_class_for_student(
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

    async def _to_attendance_out(self, row: Attendance) -> AttendanceOut:
        return AttendanceOut(
            id=row.id,
            school_id=row.school_id,
            student_id=row.student_id,
            student_name=await self._student_name(row.student_id),
            class_id=row.class_id,
            class_name=await self._class_name(row.class_id),
            attended_on=row.attended_on,
            period=row.period,
            status=row.status,
            arrival_time=row.arrival_time,
            reason=row.reason,
            leave_request_id=row.leave_request_id,
            marked_by=row.marked_by,
            marked_by_name=await self._user_name(row.marked_by),
            marked_at=row.marked_at,
            last_modified_by=row.last_modified_by,
            last_modified_at=row.last_modified_at,
            created_at=row.created_at,
        )

    async def _to_leave_out(self, row: LeaveRequest) -> LeaveRequestOut:
        enr = await self._active_class_for_student(row.school_id, row.student_id)
        return LeaveRequestOut(
            id=row.id,
            school_id=row.school_id,
            student_id=row.student_id,
            student_name=await self._student_name(row.student_id),
            class_name=await self._class_name(enr.class_id) if enr else None,
            requested_by=row.requested_by,
            start_date=row.start_date,
            end_date=row.end_date,
            reason=row.reason,
            attachment_name=row.attachment_name,
            attachment_url=row.attachment_url,
            status=row.status,
            source=row.source or "MANUAL",
            decided_by=row.decided_by,
            decision_note=row.decision_note,
            created_at=row.created_at,
        )

    async def student_for_user(self, *, school_id: UUID, user_id: UUID) -> Student:
        result = await self.db.execute(
            select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
        )
        student = result.scalar_one_or_none()
        if not student:
            raise ForbiddenError("Student profile required")
        return student

    async def parent_for_user(self, *, school_id: UUID, user_id: UUID) -> Parent:
        result = await self.db.execute(
            select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
        )
        parent = result.scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        return parent

    async def assert_parent_of(
        self, *, school_id: UUID, user_id: UUID, student_id: UUID
    ) -> None:
        parent = await self.parent_for_user(school_id=school_id, user_id=user_id)
        result = await self.db.execute(
            select(StudentParent).where(
                StudentParent.school_id == school_id,
                StudentParent.parent_id == parent.id,
                StudentParent.student_id == student_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ForbiddenError("Not linked to this student")

    async def get_settings(self, school_id: UUID) -> AttendanceSettingsOut:
        row = await self.repo.get_or_create_settings(school_id)
        return AttendanceSettingsOut.model_validate(row)

    async def update_settings(
        self, school_id: UUID, data: AttendanceSettingsUpdate
    ) -> AttendanceSettingsOut:
        row = await self.repo.get_or_create_settings(school_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        return AttendanceSettingsOut.model_validate(row)

    async def list_children(self, *, school_id: UUID, user_id: UUID) -> list[ChildOut]:
        parent = await self.parent_for_user(school_id=school_id, user_id=user_id)
        result = await self.db.execute(
            select(StudentParent).where(
                StudentParent.school_id == school_id,
                StudentParent.parent_id == parent.id,
            )
        )
        out: list[ChildOut] = []
        today = date.today()
        month_start = today.replace(day=1)
        for link in result.scalars().all():
            student = await self.db.get(Student, link.student_id)
            if not student:
                continue
            user = await self.db.get(User, student.user_id)
            enr = await self._active_class_for_student(school_id, student.id)
            month = await self.monthly_stats(
                school_id=school_id,
                student_id=student.id,
                year=today.year,
                month=today.month,
            )
            out.append(
                ChildOut(
                    student_id=student.id,
                    full_name=user.full_name if user else "Student",
                    admission_no=student.admission_no,
                    class_id=enr.class_id if enr else None,
                    class_name=await self._class_name(enr.class_id) if enr else None,
                    attendance_rate=month.rate_percent,
                )
            )
        return out

    def _count_statuses(self, statuses: list[str | None]) -> AttendanceSummaryOut:
        present = sum(1 for s in statuses if s == AttendanceStatus.PRESENT.value)
        absent = sum(1 for s in statuses if s == AttendanceStatus.ABSENT.value)
        late = sum(1 for s in statuses if s == AttendanceStatus.LATE.value)
        excused = sum(1 for s in statuses if s == AttendanceStatus.EXCUSED.value)
        half_day = sum(1 for s in statuses if s == AttendanceStatus.HALF_DAY.value)
        unmarked = sum(1 for s in statuses if s is None)
        total = len(statuses)
        marked = total - unmarked
        present_like = present + late + excused + half_day
        return AttendanceSummaryOut(
            present=present,
            absent=absent,
            late=late,
            excused=excused,
            half_day=half_day,
            unmarked=unmarked,
            total=total,
            rate_percent=_rate(present_like, marked) if marked else None,
        )

    async def roster(
        self, *, school_id: UUID, class_id: UUID, attended_on: date
    ) -> tuple[list[RosterStudentOut], AttendanceSummaryOut]:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Class not found")

        enrollments = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.school_id == school_id,
                    Enrollment.class_id == class_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()

        existing = {
            a.student_id: a
            for a in await self.repo.get_day(
                school_id=school_id, class_id=class_id, attended_on=attended_on
            )
        }

        roster: list[RosterStudentOut] = []
        for enr in enrollments:
            student = await self.db.get(Student, enr.student_id)
            if not student:
                continue
            user = await self.db.get(User, student.user_id)
            att = existing.get(student.id)
            roster.append(
                RosterStudentOut(
                    student_id=student.id,
                    full_name=user.full_name if user else "Student",
                    admission_no=student.admission_no,
                    status=att.status if att else None,
                    attendance_id=att.id if att else None,
                    arrival_time=att.arrival_time if att else None,
                    reason=att.reason if att else None,
                )
            )

        roster.sort(key=lambda r: r.full_name.lower())
        summary = self._count_statuses([r.status for r in roster])
        return roster, summary

    async def _assert_teacher_can_mark_class(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        class_id: UUID,
        attended_on: date,
    ) -> None:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if teacher is None:
            raise ForbiddenError("Teacher profile required")

        dow = attended_on.weekday()
        on_timetable = (
            await self.db.execute(
                select(TimetableEntry).where(
                    TimetableEntry.school_id == school_id,
                    TimetableEntry.teacher_id == teacher.id,
                    TimetableEntry.class_id == class_id,
                    TimetableEntry.day_of_week == dow,
                )
            )
        ).scalar_one_or_none()
        if on_timetable:
            return

        assigned = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == class_id,
                )
            )
        ).scalar_one_or_none()
        if assigned:
            return

        is_class_teacher = (
            await self.db.execute(
                select(ClassRoom).where(
                    ClassRoom.school_id == school_id,
                    ClassRoom.id == class_id,
                    ClassRoom.class_teacher_id == teacher.id,
                )
            )
        ).scalar_one_or_none()
        if is_class_teacher:
            return

        raise ForbiddenError(
            "You can only take attendance for classes on your timetable, teaching assignments, or as class teacher."
        )

    async def mark(
        self, *, school_id: UUID, user_id: UUID, data: AttendanceMarkRequest, role: str | None = None
    ) -> list[AttendanceOut]:
        classroom = await self.db.get(ClassRoom, data.class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Class not found")

        if role == UserRole.TEACHER.value:
            await self._assert_teacher_can_mark_class(
                school_id=school_id, user_id=user_id, class_id=data.class_id, attended_on=data.attended_on
            )

        settings = await self.repo.get_or_create_settings(school_id)
        enrolled_ids = {
            e.student_id
            for e in (
                await self.db.execute(
                    select(Enrollment).where(
                        Enrollment.school_id == school_id,
                        Enrollment.class_id == data.class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalars().all()
        }

        out: list[AttendanceOut] = []
        absent_student_ids: list[UUID] = []

        for item in data.marks:
            if item.student_id not in enrolled_ids:
                raise AppError(
                    f"Student {item.student_id} is not enrolled in this class",
                    code="invalid_student",
                    status_code=400,
                )
            status = item.status.value
            leave = await self.leave_repo.overlapping_approved(
                school_id=school_id,
                student_id=item.student_id,
                on_date=data.attended_on,
            )
            leave_id = leave.id if leave else None
            if leave and item.status == AttendanceStatus.ABSENT:
                status = AttendanceStatus.EXCUSED.value

            row = await self.repo.upsert(
                school_id=school_id,
                student_id=item.student_id,
                class_id=data.class_id,
                attended_on=data.attended_on,
                status=status,
                marked_by=user_id,
                arrival_time=item.arrival_time,
                reason=item.reason,
                leave_request_id=leave_id,
                audit_reason=item.reason,
            )
            if status == AttendanceStatus.ABSENT.value:
                absent_student_ids.append(item.student_id)
            out.append(await self._to_attendance_out(row))

        if settings.notify_parents_on_absent:
            for sid in absent_student_ids:
                await self._notify_parents_absent(
                    school_id=school_id,
                    student_id=sid,
                    class_name=classroom.name,
                    attended_on=data.attended_on,
                    marked_at=datetime.utcnow(),
                )

        return out

    async def _already_notified_absent(
        self, *, school_id: UUID, student_id: UUID, attended_on: date, parent_user_id: UUID
    ) -> bool:
        import json

        from src.db.models.notification import Notification

        result = await self.db.execute(
            select(Notification)
            .where(
                Notification.school_id == school_id,
                Notification.user_id == parent_user_id,
                Notification.category == "ATTENDANCE_ABSENCE",
            )
            .order_by(Notification.created_at.desc())
            .limit(30)
        )
        sid = str(student_id)
        day = attended_on.isoformat()
        for n in result.scalars().all():
            try:
                meta = json.loads(n.meta_json) if n.meta_json else {}
            except Exception:
                meta = {}
            if isinstance(meta, dict) and meta.get("attended_on") == day and meta.get("student_id") == sid:
                return True
        return False

    async def _notify_parents_absent(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        class_name: str,
        attended_on: date,
        marked_at: datetime,
    ) -> None:
        name = await self._student_name(student_id) or "Your child"
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.student_id == student_id,
                )
            )
        ).scalars().all()
        for link in links:
            parent = await self.db.get(Parent, link.parent_id)
            if not parent:
                continue
            if await self._already_notified_absent(
                school_id=school_id,
                student_id=student_id,
                attended_on=attended_on,
                parent_user_id=parent.user_id,
            ):
                continue
            await create_in_app_notification(
                self.db,
                school_id=school_id,
                user_id=parent.user_id,
                title="Attendance Alert",
                body=(
                    f"{name} was marked ABSENT today.\n"
                    f"Class: {class_name}\n"
                    f"Date: {attended_on.strftime('%d %B %Y')}\n"
                    f"Marked at: {marked_at.strftime('%I:%M %p')}"
                ),
                category="ATTENDANCE_ABSENCE",
                link_path="/parent/attendance",
                meta={
                    "student_id": str(student_id),
                    "attended_on": attended_on.isoformat(),
                    "action": "REPORT_ABSENCE",
                },
                entity_type="attendance",
                priority="HIGH",
                event_key=f"ATTENDANCE_ABSENCE:{student_id}:{attended_on.isoformat()}:{parent.user_id}",
            )

    async def list_for_student(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        date_from: date | None,
        date_to: date | None,
    ) -> list[AttendanceOut]:
        student = await self.db.get(Student, student_id)
        if not student or student.school_id != school_id:
            raise NotFoundError("Student not found")
        rows = await self.repo.list_for_student(
            school_id=school_id,
            student_id=student_id,
            date_from=date_from,
            date_to=date_to,
        )
        return [await self._to_attendance_out(r) for r in rows]

    async def monthly_stats(
        self, *, school_id: UUID, student_id: UUID, year: int, month: int
    ) -> MonthlyStatsOut:
        last_day = monthrange(year, month)[1]
        date_from = date(year, month, 1)
        date_to = date(year, month, last_day)
        rows = await self.repo.list_for_student(
            school_id=school_id,
            student_id=student_id,
            date_from=date_from,
            date_to=date_to,
            limit=40,
        )
        statuses = [r.status for r in rows]
        summary = self._count_statuses(statuses)
        present_like = summary.present + summary.late + summary.excused + summary.half_day
        marked = summary.total - summary.unmarked
        return MonthlyStatsOut(
            year=year,
            month=month,
            present=summary.present,
            absent=summary.absent,
            late=summary.late,
            excused=summary.excused,
            half_day=summary.half_day,
            total_marked=marked,
            rate_percent=_rate(present_like, marked) if marked else 100.0,
            days=[await self._to_attendance_out(r) for r in sorted(rows, key=lambda x: x.attended_on)],
        )

    async def audit_for_student(
        self, *, school_id: UUID, student_id: UUID, limit: int = 50
    ) -> list[AuditLogOut]:
        result = await self.db.execute(
            select(AttendanceAuditLog)
            .where(
                AttendanceAuditLog.school_id == school_id,
                AttendanceAuditLog.student_id == student_id,
            )
            .order_by(AttendanceAuditLog.created_at.desc())
            .limit(limit)
        )
        out: list[AuditLogOut] = []
        for row in result.scalars().all():
            out.append(
                AuditLogOut(
                    id=row.id,
                    attendance_id=row.attendance_id,
                    student_id=row.student_id,
                    student_name=await self._student_name(row.student_id),
                    attended_on=row.attended_on,
                    from_status=row.from_status,
                    to_status=row.to_status,
                    changed_by=row.changed_by,
                    changed_by_name=await self._user_name(row.changed_by),
                    reason=row.reason,
                    leave_request_id=row.leave_request_id,
                    created_at=row.created_at,
                )
            )
        return out

    async def create_leave(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        data: LeaveRequestCreate,
    ) -> LeaveRequestOut:
        student_id = data.student_id
        if role == UserRole.STUDENT.value:
            student = await self.student_for_user(school_id=school_id, user_id=user_id)
            student_id = student.id
        elif role == UserRole.PARENT.value:
            if student_id is None:
                raise AppError("student_id is required", code="validation_error", status_code=400)
            await self.assert_parent_of(
                school_id=school_id, user_id=user_id, student_id=student_id
            )
        else:
            if student_id is None:
                raise AppError("student_id is required", code="validation_error", status_code=400)
            student = await self.db.get(Student, student_id)
            if not student or student.school_id != school_id:
                raise NotFoundError("Student not found")

        row = LeaveRequest(
            school_id=school_id,
            student_id=student_id,  # type: ignore[arg-type]
            requested_by=user_id,
            start_date=data.start_date,
            end_date=data.end_date,
            reason=data.reason.strip(),
            attachment_name=data.attachment_name,
            attachment_url=data.attachment_url,
            status=LeaveRequestStatus.PENDING.value,
            source=data.source or "MANUAL",
        )
        await self.leave_repo.create(row)

        name = await self._student_name(student_id) or "Student"
        await create_in_app_notification(
            self.db,
            school_id=school_id,
            user_id=user_id,
            title="Leave request submitted",
            body=f"Leave request for {name} ({data.start_date} – {data.end_date}) is pending approval.",
            category="LEAVE_SUBMITTED",
            link_path="/parent/attendance" if role == UserRole.PARENT.value else "/student/attendance",
            entity_type="leave",
            event_key=f"LEAVE_SUBMITTED:{row.id}:{user_id}",
        )
        return await self._to_leave_out(row)

    async def list_leaves(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        status: str | None,
        student_id: UUID | None,
    ) -> list[LeaveRequestOut]:
        if role == UserRole.STUDENT.value:
            student = await self.student_for_user(school_id=school_id, user_id=user_id)
            rows = await self.leave_repo.list(
                school_id=school_id, status=status, student_id=student.id
            )
        elif role == UserRole.PARENT.value:
            children = await self.list_children(school_id=school_id, user_id=user_id)
            child_ids = {c.student_id for c in children}
            if student_id and student_id not in child_ids:
                raise ForbiddenError("Not linked to this student")
            if student_id:
                rows = await self.leave_repo.list(
                    school_id=school_id, status=status, student_id=student_id
                )
            else:
                rows = []
                for cid in child_ids:
                    rows.extend(
                        await self.leave_repo.list(
                            school_id=school_id, status=status, student_id=cid
                        )
                    )
                rows.sort(key=lambda r: r.created_at, reverse=True)
        else:
            rows = await self.leave_repo.list(
                school_id=school_id, status=status, student_id=student_id
            )
        return [await self._to_leave_out(r) for r in rows]

    async def decide_leave(
        self,
        *,
        school_id: UUID,
        user_id: UUID,
        role: str,
        leave_id: UUID,
        data: LeaveDecisionRequest,
    ) -> LeaveRequestOut:
        settings = await self.repo.get_or_create_settings(school_id)
        if role == UserRole.TEACHER.value and not settings.teacher_can_approve_leave:
            raise ForbiddenError("Teachers cannot approve leave at this school")

        row = await self.leave_repo.get(school_id=school_id, leave_id=leave_id)
        if not row:
            raise NotFoundError("Leave request not found")
        if row.status != LeaveRequestStatus.PENDING.value:
            raise AppError("Leave request already decided", code="already_decided", status_code=409)

        row.status = data.decision.value
        row.decided_by = user_id
        row.decision_note = data.decision_note

        if data.decision == LeaveRequestStatus.APPROVED:
            await self._apply_excused_for_leave(row)

        name = await self._student_name(row.student_id) or "Student"
        note_line = f"\nNote: {data.decision_note}" if data.decision_note else ""
        decision_body = (
            f"{'Leave approved' if data.decision == LeaveRequestStatus.APPROVED else 'Leave rejected'}"
            f" for {name}\n{row.start_date.strftime('%d %b')} – {row.end_date.strftime('%d %b %Y')}"
            f"{note_line}"
        )
        await create_in_app_notification(
            self.db,
            school_id=school_id,
            user_id=row.requested_by,
            title="Leave approved" if data.decision == LeaveRequestStatus.APPROVED else "Leave rejected",
            body=decision_body,
            category=(
                "LEAVE_APPROVED"
                if data.decision == LeaveRequestStatus.APPROVED
                else "LEAVE_REJECTED"
            ),
            link_path="/parent/attendance",
            entity_type="leave",
            entity_id=row.id,
            event_key=f"LEAVE_DECISION:{row.id}:{row.requested_by}",
        )
        if data.decision == LeaveRequestStatus.APPROVED:
            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == school_id,
                        StudentParent.student_id == row.student_id,
                    )
                )
            ).scalars().all()
            for link in links:
                parent = await self.db.get(Parent, link.parent_id)
                if not parent or parent.user_id == row.requested_by:
                    continue
                await create_in_app_notification(
                    self.db,
                    school_id=school_id,
                    user_id=parent.user_id,
                    title="Leave approved",
                    body=decision_body,
                    category="LEAVE_APPROVED",
                    link_path="/parent/attendance",
                    entity_type="leave",
                    entity_id=row.id,
                    event_key=f"LEAVE_APPROVED:{row.id}:{parent.user_id}",
                )

        return await self._to_leave_out(row)

    async def _apply_excused_for_leave(self, leave: LeaveRequest) -> None:
        enr = await self._active_class_for_student(leave.school_id, leave.student_id)
        if not enr:
            return
        day = leave.start_date
        while day <= leave.end_date:
            # Skip weekends — school days only
            if day.weekday() < 5:
                await self.repo.upsert(
                    school_id=leave.school_id,
                    student_id=leave.student_id,
                    class_id=enr.class_id,
                    attended_on=day,
                    status=AttendanceStatus.EXCUSED.value,
                    marked_by=leave.decided_by,
                    reason="Approved leave",
                    leave_request_id=leave.id,
                    audit_reason="Approved leave",
                )
            day += timedelta(days=1)

    async def cancel_leave(
        self, *, school_id: UUID, user_id: UUID, role: str, leave_id: UUID
    ) -> LeaveRequestOut:
        row = await self.leave_repo.get(school_id=school_id, leave_id=leave_id)
        if not row:
            raise NotFoundError("Leave request not found")
        if row.status != LeaveRequestStatus.PENDING.value:
            raise AppError("Only pending leave can be cancelled", code="not_pending", status_code=409)
        if role in (UserRole.STUDENT.value, UserRole.PARENT.value) and row.requested_by != user_id:
            if role == UserRole.PARENT.value:
                await self.assert_parent_of(school_id=school_id, user_id=user_id, student_id=row.student_id)
            else:
                raise ForbiddenError("Cannot cancel another user's leave request")
        row.status = LeaveRequestStatus.CANCELLED.value
        row.decided_by = user_id
        row.decision_note = "Cancelled by requester"
        return await self._to_leave_out(row)

    async def list_visible(
        self, user: CurrentUser, school_id: UUID, page: PageParams
    ) -> tuple[list[AttendanceOut], int]:
        stmt = (
            select(Attendance)
            .where(Attendance.school_id == school_id)
            .order_by(Attendance.attended_on.desc(), Attendance.created_at.desc())
        )
        stmt = restrict_student_rows(stmt, user, school_id, Attendance.student_id)
        result, total = await fetch_page(self.db, stmt, page)
        rows = [await self._to_attendance_out(row) for row in result.scalars().all()]
        return rows, total

    async def overview(self, *, school_id: UUID, attended_on: date) -> OverviewOut:
        classes = (
            await self.db.execute(select(ClassRoom).where(ClassRoom.school_id == school_id))
        ).scalars().all()
        present = absent = late = excused = half_day = total = 0
        low: list[ClassLowAttendanceOut] = []

        for classroom in classes:
            roster, summary = await self.roster(
                school_id=school_id, class_id=classroom.id, attended_on=attended_on
            )
            if summary.total == 0:
                continue
            present += summary.present
            absent += summary.absent
            late += summary.late
            excused += summary.excused
            half_day += summary.half_day
            total += summary.total
            marked = summary.total - summary.unmarked
            settings = await self.repo.get_or_create_settings(school_id)
            threshold = settings.low_attendance_percent or 85
            if marked > 0 and summary.rate_percent is not None and summary.rate_percent < threshold:
                low.append(
                    ClassLowAttendanceOut(
                        class_id=classroom.id,
                        class_name=classroom.name,
                        rate_percent=summary.rate_percent,
                        present=summary.present + summary.late + summary.excused + summary.half_day,
                        total=marked,
                    )
                )

        low.sort(key=lambda c: c.rate_percent)
        marked_total = present + absent + late + excused + half_day
        return OverviewOut(
            attended_on=attended_on,
            present=present,
            absent=absent,
            late=late,
            excused=excused,
            half_day=half_day,
            total=total,
            rate_percent=_rate(present + late + excused + half_day, marked_total) if marked_total else 100.0,
            low_classes=low[:10],
        )

    async def report_students(
        self, *, school_id: UUID, date_from: date, date_to: date, class_id: UUID | None = None
    ) -> list[StudentRateOut]:
        q = select(Enrollment).where(
            Enrollment.school_id == school_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
        )
        if class_id:
            q = q.where(Enrollment.class_id == class_id)
        enrollments = (await self.db.execute(q)).scalars().all()
        out: list[StudentRateOut] = []
        for enr in enrollments:
            rows = await self.repo.list_for_student(
                school_id=school_id,
                student_id=enr.student_id,
                date_from=date_from,
                date_to=date_to,
            )
            summary = self._count_statuses([r.status for r in rows])
            marked = summary.total - summary.unmarked
            if marked == 0:
                continue
            present_like = summary.present + summary.late + summary.excused + summary.half_day
            out.append(
                StudentRateOut(
                    student_id=enr.student_id,
                    student_name=await self._student_name(enr.student_id) or "Student",
                    class_name=await self._class_name(enr.class_id),
                    present=summary.present,
                    absent=summary.absent,
                    late=summary.late,
                    excused=summary.excused,
                    half_day=summary.half_day,
                    total=marked,
                    rate_percent=_rate(present_like, marked),
                )
            )
        out.sort(key=lambda r: r.rate_percent)
        return out

    async def report_classes(
        self, *, school_id: UUID, date_from: date, date_to: date
    ) -> list[ClassRateOut]:
        classes = (
            await self.db.execute(select(ClassRoom).where(ClassRoom.school_id == school_id))
        ).scalars().all()
        out: list[ClassRateOut] = []
        for classroom in classes:
            students = await self.report_students(
                school_id=school_id,
                date_from=date_from,
                date_to=date_to,
                class_id=classroom.id,
            )
            if not students:
                continue
            present = sum(s.present for s in students)
            absent = sum(s.absent for s in students)
            late = sum(s.late for s in students)
            excused = sum(s.excused for s in students)
            half_day = sum(s.half_day for s in students)
            total = sum(s.total for s in students)
            present_like = present + late + excused + half_day
            out.append(
                ClassRateOut(
                    class_id=classroom.id,
                    class_name=classroom.name,
                    present=present,
                    absent=absent,
                    late=late,
                    excused=excused,
                    half_day=half_day,
                    total=total,
                    rate_percent=_rate(present_like, total),
                )
            )
        out.sort(key=lambda r: r.rate_percent)
        return out

    async def send_daily_absence_digests(self, *, school_id: UUID, attended_on: date) -> dict:
        """
        Daily job: send parents a digest when one or more children are marked ABSENT for the day.

        - Always sends in-app notification (unless disabled by user preferences for this category)
        - Queues SMS/WhatsApp only when the parent has enabled the channel in Notification Preferences
        - Deduped via `attendance_digest_logs`
        """
        settings = await self.repo.get_or_create_settings(school_id)
        if not settings.notify_parents_on_absent:
            return {"skipped": True, "reason": "notify_parents_on_absent disabled"}

        absences = (
            await self.db.execute(
                select(Attendance).where(
                    Attendance.school_id == school_id,
                    Attendance.attended_on == attended_on,
                    Attendance.status == AttendanceStatus.ABSENT.value,
                )
            )
        ).scalars().all()

        by_parent: dict[UUID, list[tuple[str, str]]] = {}
        for a in absences:
            student_name = await self._student_name(a.student_id) or "Student"
            class_name = await self._class_name(a.class_id)
            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == school_id,
                        StudentParent.student_id == a.student_id,
                    )
                )
            ).scalars().all()
            for link in links:
                parent = await self.db.get(Parent, link.parent_id)
                if not parent:
                    continue
                by_parent.setdefault(parent.user_id, []).append((student_name, class_name))

        digests_considered = 0
        digests_sent = 0
        deliveries_queued = 0

        for parent_user_id, items in by_parent.items():
            digests_considered += 1
            kind = "ABSENCE_DAILY"
            existing = (
                await self.db.execute(
                    select(AttendanceDigestLog).where(
                        AttendanceDigestLog.user_id == parent_user_id,
                        AttendanceDigestLog.kind == kind,
                        AttendanceDigestLog.sent_on == attended_on,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue

            # record dedupe marker first
            self.db.add(
                AttendanceDigestLog(
                    school_id=school_id,
                    user_id=parent_user_id,
                    kind=kind,
                    sent_on=attended_on,
                )
            )
            await self.db.flush()

            parts = [f"{n} ({c})" for (n, c) in items[:5]]
            more = f" +{len(items) - 5} more" if len(items) > 5 else ""
            summary = ", ".join(parts) + more
            title = "Attendance update"
            body = f"Absent today ({attended_on}): {summary}."
            link_path = "/parent/attendance"

            await create_in_app_notification(
                self.db,
                school_id=school_id,
                user_id=parent_user_id,
                title=title,
                body=body,
                category=NotificationType.ATTENDANCE_ABSENCE.value,
                link_path=link_path,
                entity_type="attendance_digest",
                entity_id=None,
                priority="HIGH",
                event_key=f"ATTENDANCE_ABSENCE_DIGEST:{school_id}:{attended_on}:{parent_user_id}",
                meta={
                    "attended_on": str(attended_on),
                    "items": [{"student": n, "class": c} for n, c in items],
                },
            )

            for ch in (NotificationChannel.SMS.value, NotificationChannel.WHATSAPP.value):
                row = await queue_outbound_delivery(
                    self.db,
                    school_id=school_id,
                    user_id=parent_user_id,
                    channel=ch,
                    notification_type=NotificationType.ATTENDANCE_ABSENCE.value,
                    title=title,
                    body=body,
                    link_path=link_path,
                    meta={"attended_on": str(attended_on), "kind": kind},
                )
                if row:
                    deliveries_queued += 1

            digests_sent += 1

        await self.db.commit()
        return {
            "attendance_rows_absent": len(absences),
            "digests_considered": digests_considered,
            "digests_sent": digests_sent,
            "deliveries_queued": deliveries_queued,
        }

    async def alerts(self, *, school_id: UUID) -> list[AlertOut]:
        settings = await self.repo.get_or_create_settings(school_id)
        today = date.today()
        window_start = today - timedelta(days=settings.absence_alert_window_days)
        month_start = today.replace(day=1)
        enrollments = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.school_id == school_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()
        alerts: list[AlertOut] = []
        for enr in enrollments:
            rows = await self.repo.list_for_student(
                school_id=school_id,
                student_id=enr.student_id,
                date_from=window_start,
                date_to=today,
            )
            absences = sum(1 for r in rows if r.status == AttendanceStatus.ABSENT.value)
            lates = sum(1 for r in rows if r.status == AttendanceStatus.LATE.value)
            name = await self._student_name(enr.student_id) or "Student"
            cname = await self._class_name(enr.class_id)
            if absences >= settings.absence_alert_days:
                alerts.append(
                    AlertOut(
                        kind="ABSENCE_STREAK",
                        student_id=enr.student_id,
                        student_name=name,
                        class_name=cname,
                        message=f"{name} has {absences} absences in the last {settings.absence_alert_window_days} days.",
                        value=absences,
                    )
                )
            if lates >= settings.late_alert_count:
                alerts.append(
                    AlertOut(
                        kind="LATE_STREAK",
                        student_id=enr.student_id,
                        student_name=name,
                        class_name=cname,
                        message=f"{name} has {lates} late arrivals recently.",
                        value=lates,
                    )
                )
            month = await self.monthly_stats(
                school_id=school_id,
                student_id=enr.student_id,
                year=month_start.year,
                month=month_start.month,
            )
            if month.total_marked >= 5 and month.rate_percent < settings.low_attendance_percent:
                alerts.append(
                    AlertOut(
                        kind="LOW_ATTENDANCE",
                        student_id=enr.student_id,
                        student_name=name,
                        class_name=cname,
                        message=f"{name}'s attendance is {month.rate_percent}% (below {settings.low_attendance_percent}%).",
                        value=month.rate_percent,
                    )
                )
        return alerts
