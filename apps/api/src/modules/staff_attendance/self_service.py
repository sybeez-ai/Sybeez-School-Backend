"""Staff check-in. Student attendance is a different module and is not used here."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.core.jobs import enqueue_job
from src.core.telemetry import track_alert, track_event
from src.db.models import Employee, School, StaffAttendance, StaffAttendancePeriodLock, StaffAttendanceRegularisation, StaffAttendanceSession
from src.modules.payroll.calc import Holiday, WeeklyOff, within_geofence, working_days
from src.modules.payroll.permissions import ATTENDANCE_ADMIN, PREPARE
from src.modules.payroll.service import PayrollService
from src.modules.payroll.text import require_text

logger = logging.getLogger("sybeez.payroll.attendance")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StaffSelfAttendance:
    def __init__(self, db: AsyncSession, user: CurrentUser):
        self.db = db
        self.user = user
        self.payroll = PayrollService(db, user)

    async def _ensure(self, allowed: set[str], *, write: bool) -> None:
        """HR role and hr.read / hr.manage grants, or the payroll attendance roles."""
        from src.modules.hr.access import user_is_hr_admin

        if await user_is_hr_admin(self.db, self.user, write=write):
            await self.payroll._bind()
            return
        await self.payroll._allow(allowed)

    async def check_in(self, data: dict) -> dict:
        employee = await self.payroll._employee_for_user()
        await self.payroll._bind()
        open_row = await self._open(employee.id)
        if open_row:
            return {"session": open_row, "already_open": True}
        settings = await self._policy()
        status, flagged, reason = self._geo(settings, data, manual=bool(data.get("manual")))
        moment = _now()
        zone = await self._school_zone()
        row = StaffAttendanceSession(
            id=uuid4(),
            school_id=self.payroll.school_id,
            employee_id=employee.id,
            attendance_date=moment.astimezone(zone).date(),
            check_in_at=moment,
            server_recorded_at=moment,
            check_in_lat=_coord(data.get("lat")),
            check_in_lng=_coord(data.get("lng")),
            source="MANUAL" if data.get("manual") else "SELF",
            geofence_status=status,
            manual_reason=reason,
            is_late=self._late(settings, moment, zone),
            derived_status="OPEN",
            flagged=flagged,
        )
        self.db.add(row)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            existing = await self._open(employee.id)
            if existing:
                return {"session": existing, "already_open": True}
            raise ConflictError("Check-in is already open") from exc
        await self.payroll._audit("attendance.check_in", "attendance_session", row.id, None, {"at": moment.isoformat()}, reason)
        return {"session": row, "already_open": False}

    async def check_out(self, data: dict) -> StaffAttendanceSession:
        employee = await self.payroll._employee_for_user()
        await self.payroll._bind()
        row = await self._open(employee.id)
        if row is None:
            raise NotFoundError("There is no open check-in")
        settings = await self._policy()
        moment = _now()
        zone = await self._school_zone()
        before = {"check_out_at": None, "derived_status": row.derived_status}
        row.check_out_at = moment
        row.check_out_lat = _coord(data.get("lat"))
        row.check_out_lng = _coord(data.get("lng"))
        row.left_early = self._early(settings, moment, zone)
        row.derived_status = _derived(settings, row.check_in_at, moment)
        if data.get("lat") is not None:
            status, flagged, _reason = self._geo(settings, data, manual=False)
            if status == "OUTSIDE":
                row.flagged = True
                row.geofence_status = status
        await self._upsert_mark(employee.id, row.attendance_date, row.derived_status)
        await self.payroll._audit(
            "attendance.check_out",
            "attendance_session",
            row.id,
            before,
            {"check_out_at": moment.isoformat(), "derived_status": row.derived_status},
            None,
        )
        return row

    async def manual(self, data: dict) -> StaffAttendanceSession:
        await self.payroll._bind()
        reason = require_text(data["reason"], max_len=2000, field="reason")
        if data.get("employee_id"):
            await self._ensure(ATTENDANCE_ADMIN, write=True)
            employee = await self.payroll._employee(data["employee_id"])
        else:
            employee = await self.payroll._employee_for_user()
        settings = await self._policy()
        zone = await self._school_zone()
        start = data["check_in_at"]
        end = data["check_out_at"]
        if end <= start:
            raise AppError("Check-out must be after check-in", status_code=422)
        status, flagged, _ = self._geo(settings, data, manual=True)
        row = StaffAttendanceSession(
            id=uuid4(),
            school_id=self.payroll.school_id,
            employee_id=employee.id,
            attendance_date=start.astimezone(zone).date() if start.tzinfo else start.date(),
            check_in_at=start,
            check_out_at=end,
            server_recorded_at=_now(),
            check_in_lat=_coord(data.get("lat")),
            check_in_lng=_coord(data.get("lng")),
            source="MANUAL",
            geofence_status=status,
            manual_reason=reason,
            is_late=self._late(settings, start, zone),
            left_early=self._early(settings, end, zone),
            derived_status=_derived(settings, start, end),
            flagged=True or flagged,
        )
        self.db.add(row)
        await self._upsert_mark(employee.id, row.attendance_date, row.derived_status)
        await self.payroll._audit("attendance.manual", "attendance_session", row.id, None, {"reason": reason}, reason)
        await self.db.flush()
        return row

    async def list_mine(self, date_from: date, date_to: date) -> list[StaffAttendanceSession]:
        employee = await self.payroll._employee_for_user()
        await self.payroll._bind()
        return await self._list(date_from, date_to, employee.id)

    async def list_school(self, date_from: date, date_to: date, employee_id: UUID | None) -> list[StaffAttendanceSession]:
        await self._ensure(ATTENDANCE_ADMIN | PREPARE, write=False)
        return await self._list(date_from, date_to, employee_id)

    async def correct(self, session_id: UUID, data: dict) -> StaffAttendanceSession:
        await self._ensure(ATTENDANCE_ADMIN, write=True)
        reason = require_text(data["reason"], max_len=2000, field="reason")
        row = await self.db.get(StaffAttendanceSession, session_id)
        if row is None or row.school_id != self.payroll.school_id:
            raise NotFoundError("Attendance session not found")
        before = {
            "check_in_at": row.check_in_at.isoformat(),
            "check_out_at": row.check_out_at.isoformat() if row.check_out_at else None,
            "derived_status": row.derived_status,
        }
        if data.get("check_in_at"):
            row.check_in_at = data["check_in_at"]
            row.attendance_date = row.check_in_at.date()
        if data.get("check_out_at"):
            row.check_out_at = data["check_out_at"]
        if row.check_out_at and row.check_out_at <= row.check_in_at:
            raise AppError("Check-out must be after check-in", status_code=422)
        settings = await self._policy()
        zone = await self._school_zone()
        row.is_late = self._late(settings, row.check_in_at, zone)
        row.left_early = bool(row.check_out_at and self._early(settings, row.check_out_at, zone))
        row.derived_status = _derived(settings, row.check_in_at, row.check_out_at) if row.check_out_at else "OPEN"
        row.corrected_by = self.user.id
        row.correction_reason = reason
        row.source = "ADMIN"
        if row.check_out_at:
            await self._upsert_mark(row.employee_id, row.attendance_date, row.derived_status)
        await self.payroll._audit("attendance.correct", "attendance_session", row.id, before, {"derived_status": row.derived_status}, reason)
        return row

    async def request_regularisation(self, data: dict) -> StaffAttendanceRegularisation:
        employee = await self.payroll._employee_for_user()
        await self.payroll._bind()
        start = data["check_in_at"]
        end = data["check_out_at"]
        if end <= start:
            raise AppError("Check-out must be after check-in", status_code=422)
        row = StaffAttendanceRegularisation(
            id=uuid4(),
            school_id=self.payroll.school_id,
            employee_id=employee.id,
            attendance_date=start.date(),
            requested_check_in=start,
            requested_check_out=end,
            reason=require_text(data["reason"], max_len=2000, field="reason"),
            status="PENDING",
        )
        self.db.add(row)
        await self.payroll._audit("attendance.regularise", "regularisation", row.id, None, {"date": row.attendance_date.isoformat()}, row.reason)
        await self.db.flush()
        return row

    async def list_regularisations(self, mine: bool) -> list[StaffAttendanceRegularisation]:
        if mine:
            employee = await self.payroll._employee_for_user()
            await self.payroll._bind()
            employee_id = employee.id
        else:
            await self._ensure(ATTENDANCE_ADMIN, write=False)
            employee_id = None
        stmt = select(StaffAttendanceRegularisation).where(
            StaffAttendanceRegularisation.school_id == self.payroll.school_id
        )
        if employee_id:
            stmt = stmt.where(StaffAttendanceRegularisation.employee_id == employee_id)
        return list((await self.db.execute(stmt.order_by(StaffAttendanceRegularisation.created_at.desc()))).scalars())

    async def decide_regularisation(self, item_id: UUID, approve: bool, note: str | None) -> StaffAttendanceRegularisation:
        await self._ensure(ATTENDANCE_ADMIN, write=True)
        row = await self.db.get(StaffAttendanceRegularisation, item_id)
        if row is None or row.school_id != self.payroll.school_id:
            raise NotFoundError("Regularisation request not found")
        if row.status != "PENDING":
            raise ConflictError("This request is already decided")
        row.status = "APPROVED" if approve else "REJECTED"
        row.decided_by = self.user.id
        row.decided_at = _now()
        row.decision_note = note
        if approve:
            settings = await self._policy()
            zone = await self._school_zone()
            session = StaffAttendanceSession(
                id=uuid4(),
                school_id=row.school_id,
                employee_id=row.employee_id,
                attendance_date=row.attendance_date,
                check_in_at=row.requested_check_in,
                check_out_at=row.requested_check_out,
                server_recorded_at=_now(),
                source="REGULARISATION",
                geofence_status="DISABLED",
                manual_reason=row.reason,
                is_late=self._late(settings, row.requested_check_in, zone),
                left_early=self._early(settings, row.requested_check_out, zone),
                derived_status=_derived(settings, row.requested_check_in, row.requested_check_out),
                flagged=False,
            )
            self.db.add(session)
            await self._upsert_mark(row.employee_id, row.attendance_date, session.derived_status)
        await self.payroll._audit(
            "attendance.regularise_decide",
            "regularisation",
            row.id,
            {"status": "PENDING"},
            {"status": row.status},
            note,
        )
        return row

    async def lock_period(self, period_start: date, period_end: date, reason: str | None) -> dict:
        await self._ensure(ATTENDANCE_ADMIN | PREPARE, write=True)
        if period_end < period_start:
            raise AppError("period_end must be on or after period_start", status_code=422)
        await self.payroll.ensure_settings()
        weekly, holidays = await self.payroll._calendar()
        days = working_days(period_start, period_end, weekly, holidays)
        employees = list(
            (
                await self.db.execute(
                    select(Employee).where(Employee.school_id == self.payroll.school_id, Employee.status == "ACTIVE")
                )
            ).scalars()
        )
        gaps = []
        for employee in employees:
            marked = {
                row.attendance_date
                for row in (
                    await self.db.execute(
                        select(StaffAttendance).where(
                            StaffAttendance.employee_id == employee.id,
                            StaffAttendance.attendance_date >= period_start,
                            StaffAttendance.attendance_date <= period_end,
                        )
                    )
                ).scalars()
            }
            for day in days:
                if day not in marked:
                    gaps.append({"employee_id": str(employee.id), "date": day.isoformat()})
                    if len(gaps) >= 30:
                        break
            if len(gaps) >= 30:
                break
        if gaps:
            raise AppError(
                "Mark staff attendance for every working day before locking the period",
                code="attendance_incomplete",
                status_code=422,
            )
        existing = (
            await self.db.execute(
                select(StaffAttendancePeriodLock).where(
                    StaffAttendancePeriodLock.school_id == self.payroll.school_id,
                    StaffAttendancePeriodLock.period_start == period_start,
                    StaffAttendancePeriodLock.period_end == period_end,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = StaffAttendancePeriodLock(
                id=uuid4(),
                school_id=self.payroll.school_id,
                period_start=period_start,
                period_end=period_end,
                locked_by=self.user.id,
            )
            self.db.add(existing)
            await self.db.flush()
        try:
            await enqueue_job(
                "payroll_generate",
                {
                    "school_id": str(self.payroll.school_id),
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "locked_by": str(self.user.id),
                },
            )
        except Exception as exc:  # noqa: BLE001
            track_alert("payroll_worker_unavailable", error=type(exc).__name__)
            logger.exception("could not enqueue payroll generation")
        result = await self.payroll.generate(period_start, period_end, source="ATTENDANCE_LOCK", reason=reason or "Attendance locked")
        existing.payroll_run_id = result["run"].id
        track_event("attendance_period_locked", school_id=str(self.payroll.school_id), run_id=str(result["run"].id))
        await self.payroll._audit(
            "attendance.lock",
            "attendance_period",
            existing.id,
            None,
            {"period_start": period_start.isoformat(), "run_id": str(result["run"].id)},
            reason,
        )
        return {"lock_id": existing.id, "run_id": result["run"].id, "created": result["created"], "skipped": result["skipped"]}

    async def _policy(self):
        row = await self.payroll.load_settings()
        if row is not None:
            return row
        from datetime import time as clock
        from types import SimpleNamespace

        return SimpleNamespace(
            geofence_enabled=False,
            geofence_lat=None,
            geofence_lng=None,
            geofence_radius_m=200,
            shift_start=clock(9, 0),
            shift_end=clock(16, 0),
            grace_minutes=10,
            full_day_minutes=360,
            half_day_minutes=180,
        )

    async def _open(self, employee_id: UUID) -> StaffAttendanceSession | None:
        return (
            await self.db.execute(
                select(StaffAttendanceSession).where(
                    StaffAttendanceSession.employee_id == employee_id,
                    StaffAttendanceSession.check_out_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def _list(self, date_from: date, date_to: date, employee_id: UUID | None) -> list[StaffAttendanceSession]:
        stmt = select(StaffAttendanceSession).where(
            StaffAttendanceSession.school_id == self.payroll.school_id,
            StaffAttendanceSession.attendance_date >= date_from,
            StaffAttendanceSession.attendance_date <= date_to,
        )
        if employee_id:
            stmt = stmt.where(StaffAttendanceSession.employee_id == employee_id)
        return list((await self.db.execute(stmt.order_by(StaffAttendanceSession.check_in_at.desc()))).scalars())

    def _geo(self, settings, data: dict, *, manual: bool) -> tuple[str, bool, str | None]:
        lat = data.get("lat")
        lng = data.get("lng")
        if (lat is None) ^ (lng is None):
            raise AppError("Send both latitude and longitude, or neither", status_code=422)
        reason = None
        if manual:
            reason = require_text(data.get("reason") or "", max_len=2000, field="reason")
        status = within_geofence(
            enabled=settings.geofence_enabled,
            lat=float(lat) if lat is not None else None,
            lng=float(lng) if lng is not None else None,
            centre_lat=float(settings.geofence_lat) if settings.geofence_lat is not None else None,
            centre_lng=float(settings.geofence_lng) if settings.geofence_lng is not None else None,
            radius_m=settings.geofence_radius_m,
        )
        if settings.geofence_enabled and status in {"OUTSIDE", "NOT_CAPTURED"} and not manual:
            raise AppError(
                "You are outside the school geofence. Submit a manual entry with a reason if you still need to mark attendance.",
                status_code=422,
            )
        return status, status == "OUTSIDE", reason

    def _zone(self, school: School | None) -> ZoneInfo:
        name = school.timezone if school and school.timezone else "Asia/Kolkata"
        try:
            return ZoneInfo(name)
        except Exception:
            return ZoneInfo("Asia/Kolkata")

    async def _school_zone(self) -> ZoneInfo:
        school = await self.db.get(School, self.payroll.school_id)
        return self._zone(school)

    def _late(self, settings, moment: datetime, zone: ZoneInfo) -> bool:
        local = moment.astimezone(zone)
        start = datetime.combine(local.date(), settings.shift_start, tzinfo=zone)
        return local > start + timedelta(minutes=settings.grace_minutes)

    def _early(self, settings, moment: datetime, zone: ZoneInfo) -> bool:
        local = moment.astimezone(zone)
        end = datetime.combine(local.date(), settings.shift_end, tzinfo=zone)
        return local < end - timedelta(minutes=settings.grace_minutes)

    async def _upsert_mark(self, employee_id: UUID, day: date, status: str) -> None:
        if status not in {"PRESENT", "ABSENT", "HALF_DAY", "LEAVE"}:
            return
        row = (
            await self.db.execute(
                select(StaffAttendance).where(
                    StaffAttendance.employee_id == employee_id, StaffAttendance.attendance_date == day
                )
            )
        ).scalar_one_or_none()
        if row is None:
            self.db.add(
                StaffAttendance(
                    id=uuid4(),
                    school_id=self.payroll.school_id,
                    employee_id=employee_id,
                    attendance_date=day,
                    status=status,
                    marked_by=self.user.id,
                )
            )
        else:
            row.status = status
            row.marked_by = self.user.id


def _derived(settings, start: datetime, end: datetime | None) -> str:
    if end is None:
        return "OPEN"
    minutes = int((end - start).total_seconds() // 60)
    if minutes >= settings.full_day_minutes:
        return "PRESENT"
    if minutes >= settings.half_day_minutes:
        return "HALF_DAY"
    return "ABSENT"


def _coord(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.000001"))
