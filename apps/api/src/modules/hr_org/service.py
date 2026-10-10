"""HRM organisation structure: departments and designations (school-scoped)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import Department, Designation, Employee
from src.modules.employees.service import employee_names
from src.modules.hr_org.schemas import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    DesignationCreate,
    DesignationOut,
    DesignationUpdate,
)


class _OrgService:
    model: type[Department | Designation]
    label: str
    employee_field: str

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _list_rows(self, school_id: UUID, q: str | None, active: bool | None):
        m = self.model
        count = (
            select(func.count(Employee.id))
            .where(getattr(Employee, self.employee_field) == m.id, Employee.school_id == school_id)
            .correlate(m)
            .scalar_subquery()
        )
        stmt = select(m, count).where(m.school_id == school_id)
        if active is not None:
            stmt = stmt.where(m.is_active.is_(active))
        if q:
            like = f"%{q.strip()}%"
            conds = [m.name.ilike(like), m.description.ilike(like)]
            if m is Department:
                conds.append(Department.code.ilike(like))
            stmt = stmt.where(or_(*conds))
        return (await self.db.execute(stmt.order_by(func.lower(m.name)))).all()

    async def _get(self, school_id: UUID, item_id: UUID):
        row = (
            await self.db.execute(select(self.model).where(self.model.id == item_id, self.model.school_id == school_id))
        ).scalar_one_or_none()
        if not row:
            raise NotFoundError(f"{self.label} not found")
        return row

    async def _assigned_count(self, school_id: UUID, item_id: UUID) -> int:
        return int(
            (
                await self.db.execute(
                    select(func.count(Employee.id)).where(
                        Employee.school_id == school_id, getattr(Employee, self.employee_field) == item_id
                    )
                )
            ).scalar_one()
        )

    async def _assert_name_free(self, school_id: UUID, name: str, exclude_id: UUID | None = None) -> None:
        stmt = select(self.model.id).where(
            self.model.school_id == school_id, func.lower(self.model.name) == name.strip().lower()
        )
        if exclude_id:
            stmt = stmt.where(self.model.id != exclude_id)
        if (await self.db.execute(stmt.limit(1))).first():
            raise ConflictError(f"A {self.label.lower()} with this name already exists")

    async def _flush(self) -> None:
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError as exc:
            if "school_name" in str(exc.orig):
                raise ConflictError(f"A {self.label.lower()} with this name already exists") from exc
            raise

    async def delete(self, school_id: UUID, item_id: UUID) -> None:
        row = await self._get(school_id, item_id)
        assigned = await self._assigned_count(school_id, item_id)
        if assigned:
            noun = "employee is" if assigned == 1 else "employees are"
            raise ConflictError(
                f"{assigned} {noun} assigned to this {self.label.lower()}. "
                "Reassign them or deactivate it instead."
            )
        await self.db.delete(row)
        await self.db.flush()


class DepartmentService(_OrgService):
    model = Department
    label = "Department"
    employee_field = "department_id"

    async def list(self, school_id: UUID, *, q: str | None = None, active: bool | None = None) -> list[DepartmentOut]:
        rows = await self._list_rows(school_id, q, active)
        heads = await employee_names(self.db, school_id, {d.head_employee_id for d, _ in rows if d.head_employee_id})
        return [self._out(d, n, heads) for d, n in rows]

    async def get(self, school_id: UUID, item_id: UUID) -> DepartmentOut:
        d = await self._get(school_id, item_id)
        heads = await employee_names(self.db, school_id, {d.head_employee_id} if d.head_employee_id else set())
        return self._out(d, await self._assigned_count(school_id, d.id), heads)

    async def create(self, school_id: UUID, data: DepartmentCreate) -> DepartmentOut:
        await self._assert_name_free(school_id, data.name)
        if data.head_employee_id:
            await self._assert_employee(school_id, data.head_employee_id)
        row = Department(school_id=school_id, is_active=True, **data.model_dump())
        self.db.add(row)
        await self._flush()
        await self.db.refresh(row)
        return await self.get(school_id, row.id)

    async def update(self, school_id: UUID, item_id: UUID, data: DepartmentUpdate) -> DepartmentOut:
        row = await self._get(school_id, item_id)
        payload = data.model_dump(exclude_unset=True)
        if payload.get("name"):
            await self._assert_name_free(school_id, payload["name"], exclude_id=row.id)
        elif "name" in payload:
            payload.pop("name")
        if payload.get("head_employee_id"):
            await self._assert_employee(school_id, payload["head_employee_id"])
        if "is_active" in payload and payload["is_active"] is None:
            payload.pop("is_active")
        for k, v in payload.items():
            setattr(row, k, v)
        await self._flush()
        await self.db.refresh(row)
        return await self.get(school_id, row.id)

    async def _assert_employee(self, school_id: UUID, employee_id: UUID) -> None:
        found = (
            await self.db.execute(
                select(Employee.id).where(Employee.id == employee_id, Employee.school_id == school_id)
            )
        ).first()
        if not found:
            raise NotFoundError("Head employee not found")

    @staticmethod
    def _out(d: Department, count: int, heads: dict[UUID, str]) -> DepartmentOut:
        return DepartmentOut(
            id=d.id,
            school_id=d.school_id,
            name=d.name,
            code=d.code,
            description=d.description,
            head_employee_id=d.head_employee_id,
            head_name=heads.get(d.head_employee_id) if d.head_employee_id else None,
            is_active=d.is_active,
            employee_count=count,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )


class DesignationService(_OrgService):
    model = Designation
    label = "Designation"
    employee_field = "designation_id"

    async def list(self, school_id: UUID, *, q: str | None = None, active: bool | None = None) -> list[DesignationOut]:
        return [self._out(d, n) for d, n in await self._list_rows(school_id, q, active)]

    async def get(self, school_id: UUID, item_id: UUID) -> DesignationOut:
        d = await self._get(school_id, item_id)
        return self._out(d, await self._assigned_count(school_id, d.id))

    async def create(self, school_id: UUID, data: DesignationCreate) -> DesignationOut:
        await self._assert_name_free(school_id, data.name)
        row = Designation(school_id=school_id, is_active=True, **data.model_dump())
        self.db.add(row)
        await self._flush()
        await self.db.refresh(row)
        return await self.get(school_id, row.id)

    async def update(self, school_id: UUID, item_id: UUID, data: DesignationUpdate) -> DesignationOut:
        row = await self._get(school_id, item_id)
        payload = data.model_dump(exclude_unset=True)
        if payload.get("name"):
            await self._assert_name_free(school_id, payload["name"], exclude_id=row.id)
        elif "name" in payload:
            payload.pop("name")
        if "is_active" in payload and payload["is_active"] is None:
            payload.pop("is_active")
        for k, v in payload.items():
            setattr(row, k, v)
        await self._flush()
        await self.db.refresh(row)
        return await self.get(school_id, row.id)

    @staticmethod
    def _out(d: Designation, count: int) -> DesignationOut:
        return DesignationOut(
            id=d.id,
            school_id=d.school_id,
            name=d.name,
            description=d.description,
            is_active=d.is_active,
            employee_count=count,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
