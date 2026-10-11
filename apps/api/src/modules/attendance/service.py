from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import Attendance
from src.modules.access.scope import restrict_classes, restrict_student_rows
from src.modules.attendance.schemas import AttendanceCreate, AttendanceUpdate
from src.shared.pagination import PageParams
from src.shared.listing import fetch_page


class AttendanceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def mark(self, user: CurrentUser, school_id: UUID, data: AttendanceCreate) -> Attendance:
        row = Attendance(
            school_id=school_id,
            student_id=data.student_id,
            class_id=data.class_id,
            attended_on=data.attended_on,
            status=data.status.value,
            marked_by=user.id,
        )
        self.db.add(row)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Attendance already recorded for this student, class, and day") from exc
        return row

    async def mark_many(self, user: CurrentUser, school_id: UUID, records: list[AttendanceCreate]) -> list[Attendance]:
        rows = []
        for record in records:
            rows.append(await self.mark(user, school_id, record))
        return rows

    async def list(
        self,
        user: CurrentUser,
        school_id: UUID,
        page: PageParams,
        *,
        class_id: UUID | None = None,
        student_id: UUID | None = None,
        attended_on: date | None = None,
    ) -> tuple[list[Attendance], int]:
        stmt = select(Attendance).where(Attendance.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, Attendance.class_id)
        stmt = restrict_student_rows(stmt, user, school_id, Attendance.student_id)
        if class_id:
            stmt = stmt.where(Attendance.class_id == class_id)
        if student_id:
            stmt = stmt.where(Attendance.student_id == student_id)
        if attended_on:
            stmt = stmt.where(Attendance.attended_on == attended_on)
        stmt = stmt.order_by(Attendance.attended_on.desc(), Attendance.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def get(self, user: CurrentUser, school_id: UUID, attendance_id: UUID) -> Attendance:
        stmt = select(Attendance).where(Attendance.id == attendance_id, Attendance.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, Attendance.class_id)
        stmt = restrict_student_rows(stmt, user, school_id, Attendance.student_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Attendance record not found")
        return row

    async def update(self, user: CurrentUser, school_id: UUID, attendance_id: UUID, data: AttendanceUpdate) -> Attendance:
        row = await self.get(user, school_id, attendance_id)
        row.status = data.status.value
        row.marked_by = user.id
        await self.db.flush()
        return row
