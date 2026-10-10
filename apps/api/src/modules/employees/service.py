"""HRM employee directory: teaching staff (backed by ``teachers``) and non-teaching staff."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.db.models import Department, Designation, Employee, Teacher, User
from src.modules.employees.schemas import EmployeeCreate, EmployeeOut, EmployeeUpdate
from src.modules.teachers.schemas import TeacherCreate, TeacherUpdate
from src.modules.teachers.service import TeacherService
from src.shared.enums import PersonStatus

_HR_FIELDS = (
    "department_id",
    "designation_id",
    "employment_type",
    "address",
    "emergency_contact_name",
    "emergency_contact_phone",
    "notes",
)
_TEACHER_FIELDS = ("full_name", "phone", "employee_code", "title", "date_of_birth", "joining_date", "qualification")
_NON_TEACHING_FIELDS = ("full_name", "email", "phone", "employee_code", "date_of_birth", "joining_date", "qualification")


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
    ) -> list[EmployeeOut]:
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
                    or_(
                        (Employee.staff_type == "NON_TEACHING") & (Employee.status == "INACTIVE"),
                        (Employee.staff_type == "TEACHING") & teacher_inactive,
                    )
                )
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
        rows = (await self.db.execute(stmt.order_by(func.lower(name).asc()))).all()
        return [self._to_out(e, t, u, dept, desig) for e, t, u, dept, desig in rows]

    async def get(self, school_id: UUID, employee_id: UUID) -> EmployeeOut:
        await sync_teacher_employees(self.db, school_id)
        emp = await self._get(school_id, employee_id)
        return await self._out(emp)

    async def create(self, school_id: UUID, data: EmployeeCreate) -> EmployeeOut:
        hr = {k: getattr(data, k) for k in _HR_FIELDS}
        await self._assert_assignable(school_id, hr)
        if data.employee_code:
            await assert_employee_code_free(self.db, school_id, data.employee_code)

        if data.staff_type == "TEACHING":
            teacher = await self.teachers.create(
                school_id,
                TeacherCreate(
                    full_name=data.full_name,
                    email=data.email,
                    password=data.password,
                    employee_code=data.employee_code,
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
            out = await self._out(emp)
            out.temporary_password = teacher.temporary_password
            return out

        emp = Employee(
            school_id=school_id,
            staff_type="NON_TEACHING",
            full_name=data.full_name.strip(),
            email=str(data.email).lower() if data.email else None,
            phone=data.phone,
            employee_code=data.employee_code.strip() if data.employee_code else None,
            date_of_birth=data.date_of_birth,
            joining_date=data.joining_date,
            qualification=data.qualification,
            status="ACTIVE",
            **hr,
        )
        self.db.add(emp)
        await self.db.flush()
        await self.db.refresh(emp)
        return await self._out(emp)

    async def update(self, school_id: UUID, employee_id: UUID, data: EmployeeUpdate) -> EmployeeOut:
        emp = await self._get(school_id, employee_id)
        payload = data.model_dump(exclude_unset=True)
        await self._assert_assignable(school_id, payload, current=emp)
        if payload.get("employee_code"):
            await assert_employee_code_free(
                self.db,
                school_id,
                payload["employee_code"],
                exclude_employee_id=emp.id,
                exclude_teacher_id=emp.teacher_id,
            )

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
            if "status" in payload:
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
            if payload.get("status"):
                emp.status = payload["status"]

        await self.db.flush()
        await self.db.refresh(emp)
        return await self._out(emp)

    async def set_active(self, school_id: UUID, employee_id: UUID, active: bool) -> EmployeeOut:
        emp = await self._get(school_id, employee_id)
        if emp.staff_type == "TEACHING":
            assert emp.teacher_id is not None
            await self.teachers.set_status(
                school_id, emp.teacher_id, status="ACTIVE" if active else "INACTIVE", is_active=active
            )
        else:
            emp.status = "ACTIVE" if active else "INACTIVE"
            await self.db.flush()
        await self.db.refresh(emp)
        return await self._out(emp)

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

    async def _out(self, emp: Employee) -> EmployeeOut:
        teacher = await self.db.get(Teacher, emp.teacher_id, populate_existing=True) if emp.teacher_id else None
        user = await self.db.get(User, teacher.user_id, populate_existing=True) if teacher else None
        dept = await self.db.get(Department, emp.department_id) if emp.department_id else None
        desig = await self.db.get(Designation, emp.designation_id) if emp.designation_id else None
        return self._to_out(emp, teacher, user, dept.name if dept else None, desig.name if desig else None)

    @staticmethod
    def _to_out(
        emp: Employee,
        teacher: Teacher | None,
        user: User | None,
        department_name: str | None = None,
        designation_name: str | None = None,
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
            **hr,
        )
        if emp.staff_type == "TEACHING" and teacher is not None:
            is_active = bool(user.is_active) if user else True
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
                status=teacher.status,
                is_active=is_active and teacher.status == PersonStatus.ACTIVE.value,
            )
        return EmployeeOut(
            **common,
            user_id=emp.user_id,
            has_login=False,
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
