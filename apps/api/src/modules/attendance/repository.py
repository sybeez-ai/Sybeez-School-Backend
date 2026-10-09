from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Attendance, AttendanceAuditLog, AttendanceSettings, LeaveRequest


class AttendanceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_day(
        self, *, school_id: UUID, class_id: UUID, attended_on: date
    ) -> list[Attendance]:
        result = await self.db.execute(
            select(Attendance).where(
                Attendance.school_id == school_id,
                Attendance.class_id == class_id,
                Attendance.attended_on == attended_on,
            )
        )
        return list(result.scalars().all())

    async def list_for_student(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 120,
    ) -> list[Attendance]:
        q = select(Attendance).where(
            Attendance.school_id == school_id,
            Attendance.student_id == student_id,
        )
        if date_from:
            q = q.where(Attendance.attended_on >= date_from)
        if date_to:
            q = q.where(Attendance.attended_on <= date_to)
        result = await self.db.execute(
            q.order_by(Attendance.attended_on.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_school_day(self, *, school_id: UUID, attended_on: date) -> list[Attendance]:
        result = await self.db.execute(
            select(Attendance).where(
                Attendance.school_id == school_id,
                Attendance.attended_on == attended_on,
            )
        )
        return list(result.scalars().all())

    async def get_or_create_settings(self, school_id: UUID) -> AttendanceSettings:
        result = await self.db.execute(
            select(AttendanceSettings).where(AttendanceSettings.school_id == school_id)
        )
        row = result.scalar_one_or_none()
        if row:
            return row
        row = AttendanceSettings(school_id=school_id)
        self.db.add(row)
        await self.db.flush()
        return row

    async def upsert(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        class_id: UUID,
        attended_on: date,
        status: str,
        marked_by: UUID | None,
        arrival_time: time | None = None,
        reason: str | None = None,
        leave_request_id: UUID | None = None,
        audit_reason: str | None = None,
    ) -> Attendance:
        result = await self.db.execute(
            select(Attendance).where(
                Attendance.school_id == school_id,
                Attendance.student_id == student_id,
                Attendance.class_id == class_id,
                Attendance.attended_on == attended_on,
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.utcnow()
        from_status = row.status if row else None

        if row:
            changed = (
                row.status != status
                or row.arrival_time != arrival_time
                or (reason is not None and row.reason != reason)
            )
            row.status = status
            row.last_modified_by = marked_by
            row.last_modified_at = now
            if arrival_time is not None:
                row.arrival_time = arrival_time
            if reason is not None:
                row.reason = reason
            if leave_request_id is not None:
                row.leave_request_id = leave_request_id
            if not row.marked_by:
                row.marked_by = marked_by
                row.marked_at = now
            if changed and from_status != status:
                self.db.add(
                    AttendanceAuditLog(
                        school_id=school_id,
                        attendance_id=row.id,
                        student_id=student_id,
                        attended_on=attended_on,
                        from_status=from_status,
                        to_status=status,
                        changed_by=marked_by,
                        reason=audit_reason or reason,
                        leave_request_id=leave_request_id,
                    )
                )
            return row

        row = Attendance(
            school_id=school_id,
            student_id=student_id,
            class_id=class_id,
            attended_on=attended_on,
            status=status,
            arrival_time=arrival_time,
            reason=reason,
            leave_request_id=leave_request_id,
            marked_by=marked_by,
            marked_at=now,
            last_modified_by=marked_by,
            last_modified_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        self.db.add(
            AttendanceAuditLog(
                school_id=school_id,
                attendance_id=row.id,
                student_id=student_id,
                attended_on=attended_on,
                from_status=None,
                to_status=status,
                changed_by=marked_by,
                reason=audit_reason or reason,
                leave_request_id=leave_request_id,
            )
        )
        return row


class LeaveRequestRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, row: LeaveRequest) -> LeaveRequest:
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, *, school_id: UUID, leave_id: UUID) -> LeaveRequest | None:
        result = await self.db.execute(
            select(LeaveRequest).where(
                LeaveRequest.school_id == school_id,
                LeaveRequest.id == leave_id,
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        school_id: UUID,
        status: str | None = None,
        student_id: UUID | None = None,
        limit: int = 100,
    ) -> list[LeaveRequest]:
        q = select(LeaveRequest).where(LeaveRequest.school_id == school_id)
        if status:
            q = q.where(LeaveRequest.status == status)
        if student_id:
            q = q.where(LeaveRequest.student_id == student_id)
        result = await self.db.execute(
            q.order_by(LeaveRequest.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def overlapping_approved(
        self, *, school_id: UUID, student_id: UUID, on_date: date
    ) -> LeaveRequest | None:
        result = await self.db.execute(
            select(LeaveRequest).where(
                LeaveRequest.school_id == school_id,
                LeaveRequest.student_id == student_id,
                LeaveRequest.status == "APPROVED",
                LeaveRequest.start_date <= on_date,
                LeaveRequest.end_date >= on_date,
            )
        )
        return result.scalars().first()
