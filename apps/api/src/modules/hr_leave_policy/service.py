"""Leave policy: leave types and quotas, school holidays, working days, balances and the leave calendar.

Balances are per calendar year. A request counts against the year it starts in, measured in
working days (the school's working weekdays minus its holidays) fixed when it is submitted.
APPROVED requests are "used"; PENDING requests are held so that an employee cannot book more
than their balance while requests await a decision. MONTHLY accrual earns the annual quota in
twelve equal parts, credited at the start of each month (rounded down to half days).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.db.models import (
    Employee,
    HrHoliday,
    HrLeaveType,
    StaffLeaveRequest,
    Teacher,
    User,
)
from src.modules.employees.service import employee_code_expr
from src.modules.hr.audit import hr_audit
from src.modules.hr_leave_policy.schemas import (
    CalendarEntry,
    HolidayIn,
    HolidayOut,
    LeaveBalanceOut,
    LeaveCalendar,
    LeaveTypeIn,
    LeaveTypeOut,
    LeaveTypeUpdate,
    WorkingDays,
)
from src.modules.hr_settings.service import load_rules

ZERO = Decimal(0)
MAX_CALENDAR_DAYS = 400


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def fmt_days(x: Decimal) -> str:
    """3 -> "3", 2.5 -> "2.5", 10 -> "10" (never scientific notation)."""
    return f"{x.normalize():f}"


def employee_join(leave_model):
    """Join condition from a user-keyed leave row to its employee (needs ``Teacher`` outer-joined first)."""
    return and_(
        Employee.school_id == leave_model.school_id,
        or_(
            Employee.teacher_id == Teacher.id,
            and_(Employee.staff_type == "NON_TEACHING", Employee.user_id == leave_model.user_id),
        ),
    )


async def holidays_between(db: AsyncSession, school_id: UUID, start: date, end: date) -> dict[date, str]:
    rows = await db.execute(
        select(HrHoliday.holiday_date, HrHoliday.name).where(
            HrHoliday.school_id == school_id, HrHoliday.holiday_date.between(start, end)
        )
    )
    return dict(rows.all())


async def working_dates(db: AsyncSession, school_id: UUID, start: date, end: date) -> list[date]:
    weekdays = set((await load_rules(db, school_id)).working_weekdays)
    holidays = await holidays_between(db, school_id, start, end)
    out, d = [], start
    while d <= end:
        if d.weekday() in weekdays and d not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


async def non_working_reason(db: AsyncSession, school_id: UUID, day: date) -> str | None:
    """Why ``day`` is not a working day, or None when staff are expected to work."""
    holiday = (await holidays_between(db, school_id, day, day)).get(day)
    if holiday:
        return f"{day:%d %b %Y} is a school holiday ({holiday})"
    if day.weekday() not in (await load_rules(db, school_id)).working_weekdays:
        return f"{day:%A}s are not working days"
    return None


def request_days(row: StaffLeaveRequest) -> Decimal:
    """Working days a request covers (legacy rows without a stored count use calendar days)."""
    if row.working_days is not None:
        return _d(row.working_days)
    return Decimal((row.end_date - row.start_date).days + 1)


def entitled(lt: HrLeaveType, year: int, today: date | None = None) -> Decimal | None:
    if lt.annual_quota is None:
        return None
    quota = _d(lt.annual_quota)
    if lt.accrual != "MONTHLY":
        return quota
    today = today or date.today()
    if year < today.year:
        months = 12
    elif year > today.year:
        months = 0
    else:
        months = today.month
    earned = quota * months / 12
    return (earned * 2).to_integral_value(rounding=ROUND_FLOOR) / 2


class LeavePolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── leave types ────────────────────────────────────────────────────────

    async def list_types(self, school_id: UUID, *, active_only: bool = False) -> list[LeaveTypeOut]:
        stmt = select(HrLeaveType).where(HrLeaveType.school_id == school_id)
        if active_only:
            stmt = stmt.where(HrLeaveType.is_active.is_(True))
        rows = (await self.db.execute(stmt.order_by(HrLeaveType.sort_order, func.lower(HrLeaveType.name)))).scalars()
        return [LeaveTypeOut.model_validate(r, from_attributes=True) for r in rows]

    async def create_type(self, school_id: UUID, user_id: UUID, data: LeaveTypeIn) -> LeaveTypeOut:
        row = HrLeaveType(school_id=school_id, created_by=user_id, updated_by=user_id, **data.model_dump())
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(f"A leave type with code {data.code} already exists") from exc
        await self._audit_type(row, user_id, "created", data.model_dump())
        return await self._type_out(row)

    async def update_type(
        self, school_id: UUID, user_id: UUID, type_id: UUID, data: LeaveTypeUpdate
    ) -> LeaveTypeOut:
        row = await self.get_type(school_id, type_id)
        payload = data.model_dump(exclude_unset=True)
        for k in ("name", "accrual", "is_paid", "is_active", "sort_order"):
            if k in payload and payload[k] is None:
                raise AppError(f"{k.replace('_', ' ').capitalize()} cannot be empty", status_code=422)
        changes = {k: {"from": getattr(row, k), "to": v} for k, v in payload.items() if getattr(row, k) != v}
        for k, v in payload.items():
            setattr(row, k, v)
        row.updated_by = user_id
        await self.db.flush()
        if changes:
            await self._audit_type(row, user_id, "updated", {"changes": changes})
        return await self._type_out(row)

    async def delete_type(self, school_id: UUID, user_id: UUID, type_id: UUID) -> None:
        row = await self.get_type(school_id, type_id)
        used = await self.db.scalar(
            select(func.count()).where(StaffLeaveRequest.leave_type_id == row.id)
        )
        if used:
            raise ConflictError(
                f"{row.name} is used by {used} leave request{'s' if used != 1 else ''}; deactivate it instead"
            )
        await self._audit_type(row, user_id, "deleted", {"code": row.code})
        await self.db.delete(row)
        await self.db.flush()

    async def get_type(self, school_id: UUID, type_id: UUID) -> HrLeaveType:
        row = (
            await self.db.execute(
                select(HrLeaveType).where(HrLeaveType.id == type_id, HrLeaveType.school_id == school_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Leave type not found")
        return row

    async def has_active_types(self, school_id: UUID) -> bool:
        return bool(
            await self.db.scalar(
                select(func.count()).where(HrLeaveType.school_id == school_id, HrLeaveType.is_active.is_(True))
            )
        )

    async def _type_out(self, row: HrLeaveType) -> LeaveTypeOut:
        fresh = (
            await self.db.execute(
                select(HrLeaveType).where(HrLeaveType.id == row.id).execution_options(populate_existing=True)
            )
        ).scalar_one()
        return LeaveTypeOut.model_validate(fresh, from_attributes=True)

    async def _audit_type(self, row: HrLeaveType, user_id: UUID, verb: str, details: dict) -> None:
        await hr_audit(
            self.db,
            school_id=row.school_id,
            actor_id=user_id,
            action=f"hr.leave_type.{verb}",
            resource_type="hr_leave_type",
            resource_id=row.id,
            message=f"Leave type {row.name} {verb}",
            details=details,
        )

    # ── holidays ───────────────────────────────────────────────────────────

    async def list_holidays(self, school_id: UUID, year: int | None = None) -> list[HolidayOut]:
        stmt = select(HrHoliday).where(HrHoliday.school_id == school_id)
        if year:
            stmt = stmt.where(HrHoliday.holiday_date.between(date(year, 1, 1), date(year, 12, 31)))
        rows = (await self.db.execute(stmt.order_by(HrHoliday.holiday_date))).scalars()
        return [HolidayOut.model_validate(r, from_attributes=True) for r in rows]

    async def add_holiday(self, school_id: UUID, user_id: UUID, data: HolidayIn) -> HolidayOut:
        row = HrHoliday(school_id=school_id, holiday_date=data.holiday_date, name=data.name, created_by=user_id)
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(f"{data.holiday_date:%d %b %Y} is already a holiday") from exc
        await hr_audit(
            self.db,
            school_id=school_id,
            actor_id=user_id,
            action="hr.holiday.added",
            resource_type="hr_holiday",
            resource_id=row.id,
            message=f"Holiday {row.name} on {row.holiday_date:%d %b %Y} added",
            details={"date": row.holiday_date, "name": row.name},
        )
        return HolidayOut.model_validate(row, from_attributes=True)

    async def delete_holiday(self, school_id: UUID, user_id: UUID, holiday_id: UUID) -> None:
        row = (
            await self.db.execute(
                select(HrHoliday).where(HrHoliday.id == holiday_id, HrHoliday.school_id == school_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Holiday not found")
        await hr_audit(
            self.db,
            school_id=school_id,
            actor_id=user_id,
            action="hr.holiday.removed",
            resource_type="hr_holiday",
            resource_id=row.id,
            message=f"Holiday {row.name} on {row.holiday_date:%d %b %Y} removed",
            details={"date": row.holiday_date, "name": row.name},
        )
        await self.db.delete(row)
        await self.db.flush()

    async def working_days(self, school_id: UUID, start: date, end: date) -> WorkingDays:
        if end < start:
            raise AppError("End date must be on or after the start date", status_code=422)
        if (end - start).days > MAX_CALENDAR_DAYS:
            raise AppError(f"Date range cannot exceed {MAX_CALENDAR_DAYS} days", status_code=422)
        work = set(await working_dates(self.db, school_id, start, end))
        off = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return WorkingDays(
            start_date=start, end_date=end, working_days=len(work), non_working=[d for d in off if d not in work]
        )

    # ── balances ───────────────────────────────────────────────────────────

    async def balances(
        self, school_id: UUID, user_id: UUID, year: int, *, exclude_leave_id: UUID | None = None
    ) -> list[LeaveBalanceOut]:
        types = list(
            (
                await self.db.execute(
                    select(HrLeaveType)
                    .where(HrLeaveType.school_id == school_id)
                    .order_by(HrLeaveType.sort_order, func.lower(HrLeaveType.name))
                )
            ).scalars()
        )
        stmt = select(StaffLeaveRequest).where(
            StaffLeaveRequest.school_id == school_id,
            StaffLeaveRequest.user_id == user_id,
            StaffLeaveRequest.status.in_(("PENDING", "APPROVED")),
            StaffLeaveRequest.leave_type_id.is_not(None),
            StaffLeaveRequest.start_date.between(date(year, 1, 1), date(year, 12, 31)),
        )
        if exclude_leave_id:
            stmt = stmt.where(StaffLeaveRequest.id != exclude_leave_id)
        used: dict[UUID, Decimal] = {}
        pending: dict[UUID, Decimal] = {}
        for r in (await self.db.execute(stmt)).scalars():
            bucket = used if r.status == "APPROVED" else pending
            bucket[r.leave_type_id] = bucket.get(r.leave_type_id, ZERO) + request_days(r)
        out = []
        for lt in types:
            if not lt.is_active and lt.id not in used and lt.id not in pending:
                continue
            ent = entitled(lt, year)
            u, p = used.get(lt.id, ZERO), pending.get(lt.id, ZERO)
            out.append(
                LeaveBalanceOut(
                    leave_type_id=lt.id,
                    code=lt.code,
                    name=lt.name,
                    is_paid=lt.is_paid,
                    accrual=lt.accrual,
                    annual_quota=lt.annual_quota,
                    entitled=ent,
                    used=u,
                    pending=p,
                    available=None if ent is None else ent - u - p,
                )
            )
        return out

    async def assert_within_balance(
        self,
        school_id: UUID,
        user_id: UUID,
        lt: HrLeaveType,
        days: Decimal,
        year: int,
        *,
        exclude_leave_id: UUID | None = None,
    ) -> None:
        if lt.annual_quota is None or not (await load_rules(self.db, school_id)).leave_balance_enforced:
            return
        bal = next(
            (b for b in await self.balances(school_id, user_id, year, exclude_leave_id=exclude_leave_id)
             if b.leave_type_id == lt.id),
            None,
        )
        available = bal.available if bal and bal.available is not None else ZERO
        if days > available:
            raise AppError(
                f"Not enough {lt.name} balance for {year}: {fmt_days(days)} working day"
                f"{'s' if days != 1 else ''} requested, {fmt_days(max(available, ZERO))} available",
                status_code=422,
            )

    # ── calendar ───────────────────────────────────────────────────────────

    async def calendar(self, school_id: UUID, start: date, end: date) -> LeaveCalendar:
        if end < start:
            raise AppError("date_to must be on or after date_from", status_code=422)
        if (end - start).days > MAX_CALENDAR_DAYS:
            raise AppError(f"Date range cannot exceed {MAX_CALENDAR_DAYS} days", status_code=422)
        rows = await self.db.execute(
            select(StaffLeaveRequest, User.full_name, employee_code_expr(), HrLeaveType.name)
            .outerjoin(User, User.id == StaffLeaveRequest.user_id)
            .outerjoin(
                Teacher,
                (Teacher.user_id == StaffLeaveRequest.user_id) & (Teacher.school_id == StaffLeaveRequest.school_id),
            )
            .outerjoin(Employee, employee_join(StaffLeaveRequest))
            .outerjoin(HrLeaveType, HrLeaveType.id == StaffLeaveRequest.leave_type_id)
            .where(
                StaffLeaveRequest.school_id == school_id,
                StaffLeaveRequest.status.in_(("PENDING", "APPROVED")),
                StaffLeaveRequest.start_date <= end,
                StaffLeaveRequest.end_date >= start,
            )
            .order_by(StaffLeaveRequest.start_date)
        )
        entries = [
            CalendarEntry(
                leave_id=r.id,
                user_id=r.user_id,
                staff_name=name,
                employee_code=code,
                leave_type_name=type_name,
                status=r.status,
                start_date=r.start_date,
                end_date=r.end_date,
                working_days=request_days(r),
            )
            for r, name, code, type_name in rows.all()
        ]
        holidays = [
            h for h in await self.list_holidays(school_id) if start <= h.holiday_date <= end
        ]
        rules = await load_rules(self.db, school_id)
        return LeaveCalendar(
            date_from=start,
            date_to=end,
            working_weekdays=list(rules.working_weekdays),
            holidays=holidays,
            entries=entries,
        )
