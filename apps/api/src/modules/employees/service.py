"""HRM employee directory: teaching staff (backed by ``teachers``) and non-teaching staff."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.db.models import (
    Department,
    Designation,
    Employee,
    EmployeeHistory,
    Invitation,
    SchoolMembership,
    Teacher,
    User,
)
from src.modules.auth.cognito import generate_temporary_password
from src.modules.auth.repository import AuthRepository, hash_token
from src.modules.employees.schemas import (
    EmployeeCreate,
    EmployeeExitIn,
    EmployeeHistoryOut,
    EmployeeOut,
    EmployeeRehireIn,
    EmployeeUpdate,
    StaffLoginIn,
)
from src.modules.hr.audit import hr_audit
from src.modules.schools.provisioning import create_school_user, set_user_active
from src.modules.teachers.schemas import TeacherCreate, TeacherUpdate
from src.modules.teachers.service import TeacherService
from src.shared.enums import InvitationStatus, MembershipStatus, PersonStatus, UserRole
from src.shared.utils import generate_token, utcnow

_HR_FIELDS = (
    "department_id",
    "designation_id",
    "reporting_manager_id",
    "employment_type",
    "address",
    "emergency_contact_name",
    "emergency_contact_phone",
    "notes",
)
_TEACHER_FIELDS = ("full_name", "phone", "employee_code", "title", "date_of_birth", "joining_date", "qualification")
_NON_TEACHING_FIELDS = ("full_name", "email", "phone", "employee_code", "date_of_birth", "joining_date", "qualification")
_EXIT_FIELDS = ("exit_date", "exit_reason", "exit_notes")
_EMPLOYMENT_LABELS = {
    "FULL_TIME": "Full time",
    "PART_TIME": "Part time",
    "CONTRACT": "Contract",
    "TEMPORARY": "Temporary",
}
_PHOTO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_PHOTO_BYTES = 2_000_000
MAX_MANAGER_DEPTH = 50


async def sync_teacher_employees(db: AsyncSession, school_id: UUID) -> None:
    """Give every teacher of the school a TEACHING employee row (idempotent)."""
    await db.execute(
        text(
            "INSERT INTO employees (id, school_id, staff_type, teacher_id, user_id, status) "
            "SELECT gen_random_uuid(), t.school_id, 'TEACHING', t.id, t.user_id, 'ACTIVE' "
            "FROM teachers t WHERE t.school_id = :sid "
            "AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.teacher_id = t.id) "
            "ON CONFLICT (teacher_id) DO NOTHING"
        ),
        {"sid": school_id},
    )
    await db.execute(
        text(
            "UPDATE employees e SET user_id = t.user_id "
            "FROM teachers t "
            "WHERE e.teacher_id = t.id AND e.school_id = :sid "
            "AND e.user_id IS NULL AND t.user_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM employees other "
            "WHERE other.school_id = e.school_id AND other.user_id = t.user_id AND other.id <> e.id"
            ")"
        ),
        {"sid": school_id},
    )


def employee_name_expr():
    """Display name of an employee row; needs ``Teacher`` and ``User`` outer-joined."""
    return func.coalesce(User.full_name, Employee.full_name)


def employee_code_expr():
    """Employee code of a row; needs ``Teacher`` outer-joined."""
    return func.coalesce(Teacher.employee_code, Teacher.teacher_code, Employee.employee_code)


def employee_active_expr():
    """Whether an employee is active; needs ``Teacher`` and ``User`` outer-joined."""
    return case(
        (
            Employee.staff_type == "TEACHING",
            and_(Teacher.status == PersonStatus.ACTIVE.value, func.coalesce(User.is_active, True)),
        ),
        else_=Employee.status == "ACTIVE",
    )


async def employee_names(db: AsyncSession, school_id: UUID, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Employee.id, employee_name_expr())
        .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
        .outerjoin(User, User.id == Teacher.user_id)
        .where(Employee.school_id == school_id, Employee.id.in_(ids))
    )
    return {eid: name or "—" for eid, name in rows.all()}


async def assert_employee_code_free(
    db: AsyncSession,
    school_id: UUID,
    code: str,
    *,
    exclude_employee_id: UUID | None = None,
    exclude_teacher_id: UUID | None = None,
) -> None:
    """Employee codes are unique per school across teaching and non-teaching staff."""
    norm = code.strip().lower()
    tq = select(Teacher.id).where(Teacher.school_id == school_id, func.lower(Teacher.employee_code) == norm)
    if exclude_teacher_id:
        tq = tq.where(Teacher.id != exclude_teacher_id)
    eq = select(Employee.id).where(
        Employee.school_id == school_id,
        Employee.staff_type == "NON_TEACHING",
        func.lower(Employee.employee_code) == norm,
    )
    if exclude_employee_id:
        eq = eq.where(Employee.id != exclude_employee_id)
    if (await db.execute(tq.limit(1))).first() or (await db.execute(eq.limit(1))).first():
        raise ConflictError("Employee ID already exists")


async def next_employee_code(db: AsyncSession, school_id: UUID, prefix: str) -> str:
    """Next free ``<prefix><number>`` code across teaching and non-teaching staff."""
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$", re.IGNORECASE)
    like = f"{prefix.lower()}%"
    codes = [
        *(
            await db.scalars(
                select(Teacher.employee_code).where(
                    Teacher.school_id == school_id, func.lower(Teacher.employee_code).like(like)
                )
            )
        ),
        *(
            await db.scalars(
                select(Employee.employee_code).where(
                    Employee.school_id == school_id, func.lower(Employee.employee_code).like(like)
                )
            )
        ),
    ]
    numbers = [int(m.group(1)) for c in codes if c and (m := pattern.match(c))]
    return f"{prefix}{(max(numbers, default=0) + 1):04d}"


class EmployeeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.teachers = TeacherService(db)

    async def list(
        self,
        school_id: UUID,
        *,
        q: str | None = None,
        status: str | None = None,
        staff_type: str | None = None,
        employment_type: str | None = None,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        reporting_manager_id: UUID | None = None,
        sort: str = "name",
        order: str = "asc",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[EmployeeOut], int]:
        await sync_teacher_employees(self.db, school_id)
        stmt = (
            select(Employee, Teacher, User, Department.name, Designation.name)
            .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
            .outerjoin(User, User.id == Teacher.user_id)
            .outerjoin(Department, Department.id == Employee.department_id)
            .outerjoin(Designation, Designation.id == Employee.designation_id)
            .where(Employee.school_id == school_id)
        )
        name = employee_name_expr()
        if department_id:
            stmt = stmt.where(Employee.department_id == department_id)
        if designation_id:
            stmt = stmt.where(Employee.designation_id == designation_id)
        if reporting_manager_id:
            stmt = stmt.where(Employee.reporting_manager_id == reporting_manager_id)
        if staff_type:
            stmt = stmt.where(Employee.staff_type == staff_type.upper())
        if employment_type:
            stmt = stmt.where(Employee.employment_type == employment_type.upper())
        if status:
            su = status.upper()
            teacher_inactive = or_(Teacher.status == PersonStatus.INACTIVE.value, User.is_active.is_(False))
            if su == "ACTIVE":
                stmt = stmt.where(
                    or_(
                        (Employee.staff_type == "NON_TEACHING") & (Employee.status == "ACTIVE"),
                        (Employee.staff_type == "TEACHING")
                        & (Teacher.status == PersonStatus.ACTIVE.value)
                        & User.is_active.is_(True),
                    )
                )
            elif su == "INACTIVE":
                stmt = stmt.where(
                    Employee.status != "LEFT",
                    or_(
                        (Employee.staff_type == "NON_TEACHING") & (Employee.status == "INACTIVE"),
                        (Employee.staff_type == "TEACHING") & teacher_inactive,
                    ),
                )
            elif su == "LEFT":
                stmt = stmt.where(Employee.status == "LEFT")
            else:
                stmt = stmt.where(func.coalesce(Teacher.status, Employee.status) == su)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    name.ilike(like),
                    func.coalesce(User.email, Employee.email).ilike(like),
                    func.coalesce(Teacher.employee_code, Employee.employee_code).ilike(like),
                    Teacher.teacher_code.ilike(like),
                    func.coalesce(Teacher.phone, Employee.phone).ilike(like),
                )
            )
        total = int(await self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        sort_expr = {
            "name": func.lower(name),
            "code": func.lower(employee_code_expr()),
            "joining_date": func.coalesce(Teacher.joining_date, Employee.joining_date),
            "department": func.lower(Department.name),
            "designation": func.lower(Designation.name),
            "status": case((Employee.status == "LEFT", "LEFT"), else_=func.coalesce(Teacher.status, Employee.status)),
            "created_at": Employee.created_at,
        }[sort]
        direction = sort_expr.desc().nulls_last() if order == "desc" else sort_expr.asc().nulls_last()
        stmt = stmt.order_by(direction, func.lower(name).asc(), Employee.id)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        rows = (await self.db.execute(stmt)).all()
        managers = await employee_names(
            self.db, school_id, {e.reporting_manager_id for e, *_ in rows if e.reporting_manager_id}
        )
        logins = await self._active_staff_logins({e.user_id for e, *_ in rows if e.staff_type == "NON_TEACHING"})
        return [
            self._to_out(e, t, u, dept, desig, managers.get(e.reporting_manager_id), e.user_id in logins)
            for e, t, u, dept, desig in rows
        ], total

    async def get(self, school_id: UUID, employee_id: UUID) -> EmployeeOut:
        await sync_teacher_employees(self.db, school_id)
        emp = await self._get(school_id, employee_id)
        return await self._out(emp)

    async def create(self, school_id: UUID, actor: UUID | None, data: EmployeeCreate) -> EmployeeOut:
        from src.modules.hr_settings.service import load_rules

        hr = {k: getattr(data, k) for k in _HR_FIELDS}
        await self._assert_assignable(school_id, hr)
        if hr["reporting_manager_id"]:
            await self._assert_manager(school_id, hr["reporting_manager_id"], None)
        code = data.employee_code.strip() if data.employee_code else None
        if code:
            await assert_employee_code_free(self.db, school_id, code)
        else:
            prefix = (await load_rules(self.db, school_id)).employee_code_prefix
            if prefix:
                code = await next_employee_code(self.db, school_id, prefix)

        password = data.password
        if data.grant_access and not password:
            password = generate_temporary_password()
        if data.staff_type == "TEACHING":
            teacher = await self.teachers.create(
                school_id,
                TeacherCreate(
                    full_name=data.full_name,
                    email=data.email,
                    password=password,
                    employee_code=code,
                    title=data.title,
                    phone=data.phone,
                    date_of_birth=data.date_of_birth,
                    joining_date=data.joining_date,
                    qualification=data.qualification,
                ),
            )
            emp = Employee(
                school_id=school_id,
                staff_type="TEACHING",
                teacher_id=teacher.id,
                user_id=teacher.user_id,
                status="ACTIVE",
                **hr,
            )
            self.db.add(emp)
            await self.db.flush()
            temp = teacher.temporary_password
            joined = teacher.joining_date
        else:
            emp = Employee(
                school_id=school_id,
                staff_type="NON_TEACHING",
                full_name=data.full_name.strip(),
                email=str(data.email).lower() if data.email else None,
                phone=data.phone,
                employee_code=code,
                date_of_birth=data.date_of_birth,
                joining_date=data.joining_date,
                qualification=data.qualification,
                status="ACTIVE",
                **hr,
            )
            self.db.add(emp)
            await self.db.flush()
            await self.db.refresh(emp)
            temp = None
            joined = data.joining_date
            if data.grant_access:
                granted = await self.grant_login(
                    school_id,
                    actor,
                    emp.id,
                    StaffLoginIn(email=emp.email, password=password),
                )
                temp = granted.temporary_password
        invite = None
        if data.grant_access:
            role = UserRole.TEACHER if data.staff_type == "TEACHING" else UserRole.STAFF
            invite = await self._issue_invite(school_id, actor, str(data.email), role, emp.id)
        self._history(emp, "JOINED", joined or date.today(), actor, to_value=data.staff_type.replace("_", "-").lower())
        await self.db.flush()
        out = await self._out(emp)
        await self._audit(emp, actor, "created", f"{out.full_name} added to HRM", {"staff_type": emp.staff_type})
        out.temporary_password = temp
        out.invite_token = invite
        return out

    async def update(
        self, school_id: UUID, actor: UUID | None, employee_id: UUID, data: EmployeeUpdate
    ) -> EmployeeOut:
        emp = await self._get(school_id, employee_id)
        payload = data.model_dump(exclude_unset=True)
        effective = payload.pop("effective_date", None) or date.today()
        note = payload.pop("change_note", None)
        if effective > date.today():
            raise AppError("The effective date cannot be in the future", status_code=422)
        if payload.get("status") == "LEFT" and emp.status != "LEFT":
            raise AppError("Use the exit action to record that an employee has left", status_code=422)
        if "status" in payload and emp.status == "LEFT" and payload["status"] != "LEFT":
            raise AppError("Use the rehire action to bring back an employee who has left", status_code=422)
        await self._assert_assignable(school_id, payload, current=emp)
        if payload.get("reporting_manager_id") and payload["reporting_manager_id"] != emp.reporting_manager_id:
            await self._assert_manager(school_id, payload["reporting_manager_id"], emp.id)
        if payload.get("employee_code"):
            await assert_employee_code_free(
                self.db,
                school_id,
                payload["employee_code"],
                exclude_employee_id=emp.id,
                exclude_teacher_id=emp.teacher_id,
            )
        before = await self._out(emp)

        for k in _HR_FIELDS:
            if k in payload:
                setattr(emp, k, payload[k])

        if emp.staff_type == "TEACHING":
            assert emp.teacher_id is not None
            if "email" in payload:
                current = (await self.teachers.get(school_id, emp.teacher_id)).email
                if payload["email"] and str(payload["email"]).lower() != (current or "").lower():
                    raise AppError("A teacher's login email cannot be changed from HRM", status_code=422)
            teacher_patch = {k: payload[k] for k in _TEACHER_FIELDS if k in payload}
            if "status" in payload and payload["status"] != "LEFT":
                teacher_patch["status"] = payload["status"]
            if teacher_patch:
                await self.teachers.update(school_id, emp.teacher_id, TeacherUpdate(**teacher_patch))
        else:
            for k in _NON_TEACHING_FIELDS:
                if k not in payload:
                    continue
                v = payload[k]
                if k == "full_name" and not v:
                    raise AppError("Name is required", status_code=422)
                if k == "email" and v:
                    v = str(v).lower()
                setattr(emp, k, v)
            if payload.get("status") and payload["status"] != emp.status:
                emp.status = payload["status"]
                await self._set_login_active(emp, emp.status == "ACTIVE")

        await self.db.flush()
        await self.db.refresh(emp)
        after = await self._out(emp)
        changes = self._record_changes(emp, before, after, effective, actor, note)
        if before.is_active != after.is_active:
            self._history(emp, "REACTIVATED" if after.is_active else "DEACTIVATED", effective, actor, note=note)
        await self.db.flush()
        diff = {
            k: {"from": getattr(before, k), "to": getattr(after, k)}
            for k in (*_HR_FIELDS, *_NON_TEACHING_FIELDS, "title", "status")
            if getattr(before, k, None) != getattr(after, k, None)
        }
        if diff:
            await self._audit(emp, actor, "updated", f"{after.full_name} updated", {"changes": diff, "history": changes})
        return after

    async def set_active(
        self, school_id: UUID, actor: UUID | None, employee_id: UUID, active: bool
    ) -> EmployeeOut:
        emp = await self._get(school_id, employee_id)
        if emp.status == "LEFT":
            raise AppError("This employee has left. Use rehire to bring them back.", status_code=422)
        before = await self._out(emp)
        if emp.staff_type == "TEACHING":
            assert emp.teacher_id is not None
            await self.teachers.set_status(
                school_id, emp.teacher_id, status="ACTIVE" if active else "INACTIVE", is_active=active
            )
        else:
            emp.status = "ACTIVE" if active else "INACTIVE"
            await self._set_login_active(emp, active)
            await self.db.flush()
        await self.db.refresh(emp)
        out = await self._out(emp)
        if before.is_active != out.is_active:
            self._history(emp, "REACTIVATED" if active else "DEACTIVATED", date.today(), actor)
            await self.db.flush()
            await self._audit(emp, actor, "activated" if active else "deactivated", f"{out.full_name} {'activated' if active else 'deactivated'}")
        return out

    async def exit(
        self, school_id: UUID, actor: UUID | None, employee_id: UUID, data: EmployeeExitIn
    ) -> EmployeeOut:
        """Record that an employee has left. Nothing is deleted: history, files and records stay."""
        emp = await self._get(school_id, employee_id)
        if emp.status == "LEFT":
            raise ConflictError("This employee has already left")
        current = await self._out(emp)
        if current.joining_date and data.exit_date < current.joining_date:
            raise AppError("Exit date cannot be before the joining date", status_code=422)
        emp.status = "LEFT"
        emp.exit_date = data.exit_date
        emp.exit_reason = data.exit_reason
        emp.exit_notes = data.notes
        if emp.staff_type == "TEACHING":
            assert emp.teacher_id is not None
            await self.teachers.set_status(school_id, emp.teacher_id, status="INACTIVE", is_active=False)
            if emp.user_id:
                await AuthRepository(self.db).revoke_all_for_user(emp.user_id)
        else:
            await self._set_login_active(emp, False)
        reports = list(
            (
                await self.db.execute(
                    select(Employee).where(
                        Employee.school_id == school_id, Employee.reporting_manager_id == emp.id
                    )
                )
            ).scalars()
        )
        for r in reports:
            r.reporting_manager_id = None
            self._history(r, "MANAGER_CHANGED", data.exit_date, actor, from_value=current.full_name, note="Manager left")
        self._history(
            emp, "EXITED", data.exit_date, actor, to_value=data.exit_reason.replace("_", " ").title(), note=data.notes
        )
        await self.db.flush()
        await self.db.refresh(emp)
        out = await self._out(emp)
        await self._audit(
            emp,
            actor,
            "exited",
            f"{out.full_name} left on {data.exit_date:%d %b %Y}",
            {"exit_date": data.exit_date, "reason": data.exit_reason, "reports_unassigned": [r.id for r in reports]},
        )
        return out

    async def rehire(
        self, school_id: UUID, actor: UUID | None, employee_id: UUID, data: EmployeeRehireIn
    ) -> EmployeeOut:
        emp = await self._get(school_id, employee_id)
        if emp.status != "LEFT":
            raise AppError("Only employees who have left can be rehired", status_code=422)
        if emp.exit_date and data.rejoin_date < emp.exit_date:
            raise AppError("Rejoin date cannot be before the exit date", status_code=422)
        previous = {k: getattr(emp, k) for k in _EXIT_FIELDS}
        emp.status = "ACTIVE"
        for k in _EXIT_FIELDS:
            setattr(emp, k, None)
        if emp.staff_type == "TEACHING":
            assert emp.teacher_id is not None
            await self.teachers.set_status(school_id, emp.teacher_id, status="ACTIVE", is_active=True)
        else:
            await self._set_login_active(emp, True)
        self._history(emp, "REHIRED", data.rejoin_date, actor, note=data.note)
        await self.db.flush()
        await self.db.refresh(emp)
        out = await self._out(emp)
        await self._audit(emp, actor, "rehired", f"{out.full_name} rehired", {"rejoin_date": data.rejoin_date, "previous_exit": previous})
        return out

    async def history(self, school_id: UUID, employee_id: UUID) -> list[EmployeeHistoryOut]:
        await self._get(school_id, employee_id)
        rows = await self.db.execute(
            select(EmployeeHistory, User.full_name)
            .outerjoin(User, User.id == EmployeeHistory.actor_user_id)
            .where(EmployeeHistory.school_id == school_id, EmployeeHistory.employee_id == employee_id)
            .order_by(EmployeeHistory.effective_date.desc(), EmployeeHistory.created_at.desc())
        )
        return [
            EmployeeHistoryOut(
                id=h.id,
                event_type=h.event_type,
                effective_date=h.effective_date,
                from_value=h.from_value,
                to_value=h.to_value,
                note=h.note,
                actor_name=actor,
                created_at=h.created_at,
            )
            for h, actor in rows.all()
        ]

    async def upload_photo(
        self, school_id: UUID, actor: UUID | None, employee_id: UUID, file: UploadFile
    ) -> EmployeeOut:
        emp = await self._get(school_id, employee_id)
        if emp.staff_type == "TEACHING":
            assert emp.teacher_id is not None
            await self.teachers.upload_photo(school_id, emp.teacher_id, file)
        else:
            ext = _PHOTO_TYPES.get((file.content_type or "").lower())
            if not ext:
                raise AppError("Photo must be PNG, JPG, or WEBP", status_code=422)
            data = await file.read()
            if not data:
                raise AppError("Empty upload", status_code=422)
            if len(data) > MAX_PHOTO_BYTES:
                raise AppError("Photo too large (max 2MB)", status_code=422)
            photo_dir = Path(__file__).resolve().parents[3] / "media" / "employee-photos"
            photo_dir.mkdir(parents=True, exist_ok=True)
            for old in photo_dir.glob(f"{emp.id}.*"):
                old.unlink(missing_ok=True)
            filename = f"{emp.id}{ext}"
            (photo_dir / filename).write_bytes(data)
            emp.photo_url = f"/media/employee-photos/{filename}"
            await self.db.flush()
        await self.db.refresh(emp)
        out = await self._out(emp)
        await self._audit(emp, actor, "photo_updated", f"Photo updated for {out.full_name}")
        return out

    async def grant_login(
        self, school_id: UUID, actor: UUID | None, employee_id: UUID, data: StaffLoginIn
    ) -> EmployeeOut:
        """Give a non-teaching employee a STAFF portal login (self-service only)."""
        emp = await self._get(school_id, employee_id)
        if emp.staff_type != "NON_TEACHING":
            raise AppError("Teaching staff sign in through their teacher account", status_code=422)
        if emp.status != "ACTIVE":
            raise AppError("Only active employees can be given a login", status_code=422)
        email = str(data.email).lower()
        password = data.password or generate_temporary_password()
        temp = None
        if emp.user_id:
            if await self._active_staff_logins({emp.user_id}):
                raise ConflictError("This employee already has a login")
            user = await self.db.get(User, emp.user_id)
            if user is None or user.email != email:
                raise AppError(
                    f"This employee's login is {user.email if user else 'missing'}; re-enable it with the same email",
                    status_code=422,
                )
            await self._set_login_active(emp, True)
        else:
            if await self.db.scalar(select(User.id).where(func.lower(User.email) == email)):
                raise ConflictError("An account with this email already exists")
            user, _, temp = await create_school_user(
                self.db,
                school_id=school_id,
                email=email,
                full_name=emp.full_name or email,
                password=password,
                role=UserRole.STAFF,
                actor_id=actor,
            )
            emp.user_id = user.id
            if not emp.email:
                emp.email = email
        await self.db.flush()
        await self.db.refresh(emp)
        out = await self._out(emp)
        await self._audit(emp, actor, "login_granted", f"Staff login enabled for {out.full_name}", {"email": email})
        out.temporary_password = temp
        return out

    async def _issue_invite(
        self, school_id: UUID, actor: UUID | None, email: str, role: UserRole, employee_id: UUID
    ) -> str:
        """Return a one-time invite token. The temporary password is the immediate sign-in secret."""
        raw = generate_token(24)
        self.db.add(
            Invitation(
                school_id=school_id,
                email=email.lower(),
                role=role.value,
                token_hash=hash_token(raw),
                status=InvitationStatus.PENDING.value,
                invited_by=actor,
                expires_at=utcnow() + timedelta(days=7),
                message="Portal access granted from HRM",
            )
        )
        await self.db.flush()
        await hr_audit(
            self.db,
            school_id=school_id,
            actor_id=actor,
            action="hr.employee.invite",
            resource_type="employee",
            resource_id=employee_id,
            message=f"Portal invite issued for {email.lower()}",
            details={"role": role.value, "portal": "teacher" if role == UserRole.TEACHER else "staff"},
        )
        return raw

    async def revoke_login(self, school_id: UUID, actor: UUID | None, employee_id: UUID) -> EmployeeOut:
        emp = await self._get(school_id, employee_id)
        if emp.staff_type != "NON_TEACHING" or not emp.user_id:
            raise AppError("This employee has no staff login", status_code=422)
        await self._set_login_active(emp, False)
        await self.db.flush()
        await self.db.refresh(emp)
        out = await self._out(emp)
        await self._audit(emp, actor, "login_revoked", f"Staff login disabled for {out.full_name}")
        return out

    # ── internals ──────────────────────────────────────────────────────────

    async def _get(self, school_id: UUID, employee_id: UUID) -> Employee:
        emp = (
            await self.db.execute(
                select(Employee).where(Employee.id == employee_id, Employee.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not emp:
            raise NotFoundError("Employee not found")
        return emp

    async def _assert_assignable(self, school_id: UUID, payload: dict, current: Employee | None = None) -> None:
        """Department/designation must belong to the school and be active (unless unchanged)."""
        for field, model, label in (
            ("department_id", Department, "Department"),
            ("designation_id", Designation, "Designation"),
        ):
            target = payload.get(field)
            if not target or (current is not None and getattr(current, field) == target):
                continue
            row = (
                await self.db.execute(select(model).where(model.id == target, model.school_id == school_id))
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"{label} not found")
            if not row.is_active:
                raise AppError(f"{label} '{row.name}' is inactive", status_code=422)

    async def _assert_manager(self, school_id: UUID, manager_id: UUID, employee_id: UUID | None) -> None:
        """A manager is an active employee of the same school and the chain has no cycles."""
        if manager_id == employee_id:
            raise AppError("An employee cannot report to themselves", status_code=422)
        row = (
            await self.db.execute(
                select(Employee.id, employee_active_expr(), Employee.status)
                .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
                .outerjoin(User, User.id == Teacher.user_id)
                .where(Employee.id == manager_id, Employee.school_id == school_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Reporting manager not found")
        if not row[1] or row[2] == "LEFT":
            raise AppError("The reporting manager must be an active employee", status_code=422)
        if employee_id is None:
            return
        cursor: UUID | None = manager_id
        for _ in range(MAX_MANAGER_DEPTH):
            cursor = await self.db.scalar(
                select(Employee.reporting_manager_id).where(Employee.id == cursor)
            )
            if cursor is None:
                return
            if cursor == employee_id:
                raise AppError(
                    "That manager already reports (directly or indirectly) to this employee",
                    status_code=422,
                )
        raise AppError("The reporting chain is too deep", status_code=422)

    async def _active_staff_logins(self, user_ids: set[UUID | None]) -> set[UUID]:
        ids = {u for u in user_ids if u}
        if not ids:
            return set()
        rows = await self.db.scalars(
            select(SchoolMembership.user_id)
            .join(User, User.id == SchoolMembership.user_id)
            .where(
                SchoolMembership.user_id.in_(ids),
                SchoolMembership.role == UserRole.STAFF.value,
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
                User.is_active.is_(True),
            )
        )
        return set(rows)

    async def _set_login_active(self, emp: Employee, active: bool) -> None:
        """Enable/disable a non-teaching employee's STAFF login (no-op without one)."""
        if emp.staff_type != "NON_TEACHING" or not emp.user_id:
            return
        await self.db.execute(
            update(SchoolMembership)
            .where(
                SchoolMembership.user_id == emp.user_id,
                SchoolMembership.school_id == emp.school_id,
                SchoolMembership.role == UserRole.STAFF.value,
            )
            .values(status=(MembershipStatus.ACTIVE if active else MembershipStatus.SUSPENDED).value)
        )
        user = await self.db.get(User, emp.user_id)
        if user is None:
            return
        if not active:
            await AuthRepository(self.db).revoke_all_for_user(user.id)
            other = await self.db.scalar(
                select(func.count()).where(
                    SchoolMembership.user_id == user.id,
                    SchoolMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
            if not other:
                await set_user_active(self.db, user=user, active=False)
        elif not user.is_active:
            await set_user_active(self.db, user=user, active=True)

    def _history(
        self,
        emp: Employee,
        event: str,
        effective: date,
        actor: UUID | None,
        *,
        from_value: str | None = None,
        to_value: str | None = None,
        note: str | None = None,
    ) -> dict:
        self.db.add(
            EmployeeHistory(
                school_id=emp.school_id,
                employee_id=emp.id,
                event_type=event,
                effective_date=effective,
                from_value=(from_value or None) and from_value[:255],
                to_value=(to_value or None) and to_value[:255],
                note=note,
                actor_user_id=actor,
            )
        )
        return {"event": event, "from": from_value, "to": to_value, "effective": effective}

    def _record_changes(
        self,
        emp: Employee,
        before: EmployeeOut,
        after: EmployeeOut,
        effective: date,
        actor: UUID | None,
        note: str | None,
    ) -> list[dict]:
        out = []
        for event, attr in (
            ("DEPARTMENT_CHANGED", "department_name"),
            ("DESIGNATION_CHANGED", "designation_name"),
            ("MANAGER_CHANGED", "reporting_manager_name"),
        ):
            id_attr = attr.replace("_name", "_id")
            if getattr(before, id_attr) != getattr(after, id_attr):
                out.append(
                    self._history(
                        emp, event, effective, actor, from_value=getattr(before, attr), to_value=getattr(after, attr), note=note
                    )
                )
        if before.employment_type != after.employment_type:
            out.append(
                self._history(
                    emp,
                    "EMPLOYMENT_TYPE_CHANGED",
                    effective,
                    actor,
                    from_value=_EMPLOYMENT_LABELS.get(before.employment_type or ""),
                    to_value=_EMPLOYMENT_LABELS.get(after.employment_type or ""),
                    note=note,
                )
            )
        return out

    async def _audit(
        self, emp: Employee, actor: UUID | None, verb: str, message: str, details: dict | None = None
    ) -> None:
        await hr_audit(
            self.db,
            school_id=emp.school_id,
            actor_id=actor,
            action=f"hr.employee.{verb}",
            resource_type="employee",
            resource_id=emp.id,
            message=message,
            details=details,
        )

    async def _out(self, emp: Employee) -> EmployeeOut:
        teacher = await self.db.get(Teacher, emp.teacher_id, populate_existing=True) if emp.teacher_id else None
        user = await self.db.get(User, teacher.user_id, populate_existing=True) if teacher else None
        dept = await self.db.get(Department, emp.department_id) if emp.department_id else None
        desig = await self.db.get(Designation, emp.designation_id) if emp.designation_id else None
        manager = (
            (await employee_names(self.db, emp.school_id, {emp.reporting_manager_id})).get(emp.reporting_manager_id)
            if emp.reporting_manager_id
            else None
        )
        has_login = bool(
            emp.staff_type == "NON_TEACHING" and await self._active_staff_logins({emp.user_id})
        )
        return self._to_out(
            emp, teacher, user, dept.name if dept else None, desig.name if desig else None, manager, has_login
        )

    @staticmethod
    def _to_out(
        emp: Employee,
        teacher: Teacher | None,
        user: User | None,
        department_name: str | None = None,
        designation_name: str | None = None,
        manager_name: str | None = None,
        staff_login: bool = False,
    ) -> EmployeeOut:
        hr = {k: getattr(emp, k) for k in _HR_FIELDS}
        common = dict(
            id=emp.id,
            school_id=emp.school_id,
            staff_type=emp.staff_type,
            teacher_id=emp.teacher_id,
            created_at=emp.created_at,
            updated_at=emp.updated_at,
            department_name=department_name,
            designation_name=designation_name,
            reporting_manager_name=manager_name,
            exit_date=emp.exit_date,
            exit_reason=emp.exit_reason,
            exit_notes=emp.exit_notes,
            **hr,
        )
        if emp.staff_type == "TEACHING" and teacher is not None:
            is_active = bool(user.is_active) if user else True
            active = is_active and teacher.status == PersonStatus.ACTIVE.value
            return EmployeeOut(
                **common,
                user_id=teacher.user_id,
                has_login=user is not None,
                full_name=(user.full_name if user else None) or "—",
                email=user.email if user else None,
                phone=teacher.phone or (user.phone if user else None),
                employee_code=teacher.employee_code or teacher.teacher_code,
                title=teacher.title,
                date_of_birth=teacher.date_of_birth,
                joining_date=teacher.joining_date,
                qualification=teacher.qualification,
                photo_url=teacher.photo_url or (user.avatar_url if user else None),
                status="LEFT" if emp.status == "LEFT" and not active else teacher.status,
                is_active=active,
            )
        return EmployeeOut(
            **common,
            user_id=emp.user_id,
            has_login=staff_login,
            full_name=emp.full_name or "—",
            email=emp.email,
            phone=emp.phone,
            employee_code=emp.employee_code,
            title=None,
            date_of_birth=emp.date_of_birth,
            joining_date=emp.joining_date,
            qualification=emp.qualification,
            photo_url=emp.photo_url,
            status=emp.status,
            is_active=emp.status == "ACTIVE",
        )
