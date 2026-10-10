from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.employees.schemas import EmployeeCreate, EmployeeOut, EmployeeUpdate
from src.modules.employees.service import EmployeeService
from src.shared.enums import UserRole

router = APIRouter(prefix="/hr/employees", tags=["hr-employees"])

Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[EmployeeOut])
async def list_employees(
    user: Admin,
    db: Db,
    q: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, pattern="^(ACTIVE|INACTIVE|LEFT)$"),
    staff_type: str | None = Query(default=None, pattern="^(TEACHING|NON_TEACHING)$"),
    employment_type: str | None = Query(default=None, pattern="^(FULL_TIME|PART_TIME|CONTRACT|TEMPORARY)$"),
    department_id: UUID | None = None,
    designation_id: UUID | None = None,
):
    return await EmployeeService(db).list(
        _sid(user),
        q=q,
        status=status,
        staff_type=staff_type,
        employment_type=employment_type,
        department_id=department_id,
        designation_id=designation_id,
    )


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(body: EmployeeCreate, user: Admin, db: Db):
    return await EmployeeService(db).create(_sid(user), body)


@router.get("/{employee_id}", response_model=EmployeeOut)
async def get_employee(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).get(_sid(user), employee_id)


@router.patch("/{employee_id}", response_model=EmployeeOut)
async def update_employee(employee_id: UUID, body: EmployeeUpdate, user: Admin, db: Db):
    return await EmployeeService(db).update(_sid(user), employee_id, body)


@router.post("/{employee_id}/activate", response_model=EmployeeOut)
async def activate_employee(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).set_active(_sid(user), employee_id, True)


@router.post("/{employee_id}/deactivate", response_model=EmployeeOut)
async def deactivate_employee(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).set_active(_sid(user), employee_id, False)
