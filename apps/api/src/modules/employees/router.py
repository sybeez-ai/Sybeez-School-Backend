from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.employees.schemas import (
    EmployeeCreate,
    EmployeeExitIn,
    EmployeeHistoryOut,
    EmployeeOut,
    EmployeeRehireIn,
    EmployeeSort,
    EmployeeUpdate,
    StaffLoginIn,
)
from src.modules.employees.service import EmployeeService
router = APIRouter(prefix="/hr/employees", tags=["hr-employees"])

Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[EmployeeOut])
async def list_employees(
    user: Admin,
    db: Db,
    response: Response,
    q: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, pattern="^(ACTIVE|INACTIVE|LEFT)$"),
    staff_type: str | None = Query(default=None, pattern="^(TEACHING|NON_TEACHING)$"),
    employment_type: str | None = Query(default=None, pattern="^(FULL_TIME|PART_TIME|CONTRACT|TEMPORARY)$"),
    department_id: UUID | None = None,
    designation_id: UUID | None = None,
    reporting_manager_id: UUID | None = None,
    sort: EmployeeSort = "name",
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    items, total = await EmployeeService(db).list(
        _sid(user),
        q=q,
        status=status,
        staff_type=staff_type,
        employment_type=employment_type,
        department_id=department_id,
        designation_id=designation_id,
        reporting_manager_id=reporting_manager_id,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(body: EmployeeCreate, user: Admin, db: Db):
    return await EmployeeService(db).create(_sid(user), user.id, body)


@router.get("/{employee_id}", response_model=EmployeeOut)
async def get_employee(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).get(_sid(user), employee_id)


@router.patch("/{employee_id}", response_model=EmployeeOut)
async def update_employee(employee_id: UUID, body: EmployeeUpdate, user: Admin, db: Db):
    return await EmployeeService(db).update(_sid(user), user.id, employee_id, body)


@router.post("/{employee_id}/activate", response_model=EmployeeOut)
async def activate_employee(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).set_active(_sid(user), user.id, employee_id, True)


@router.post("/{employee_id}/deactivate", response_model=EmployeeOut)
async def deactivate_employee(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).set_active(_sid(user), user.id, employee_id, False)


@router.post("/{employee_id}/exit", response_model=EmployeeOut)
async def exit_employee(employee_id: UUID, body: EmployeeExitIn, user: Admin, db: Db):
    return await EmployeeService(db).exit(_sid(user), user.id, employee_id, body)


@router.post("/{employee_id}/rehire", response_model=EmployeeOut)
async def rehire_employee(employee_id: UUID, body: EmployeeRehireIn, user: Admin, db: Db):
    return await EmployeeService(db).rehire(_sid(user), user.id, employee_id, body)


@router.get("/{employee_id}/history", response_model=list[EmployeeHistoryOut])
async def employee_history(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).history(_sid(user), employee_id)


@router.post("/{employee_id}/photo", response_model=EmployeeOut)
async def upload_employee_photo(
    employee_id: UUID, user: Admin, db: Db, file: Annotated[UploadFile, File()]
):
    return await EmployeeService(db).upload_photo(_sid(user), user.id, employee_id, file)


@router.post("/{employee_id}/login", response_model=EmployeeOut)
async def grant_staff_login(employee_id: UUID, body: StaffLoginIn, user: Admin, db: Db):
    return await EmployeeService(db).grant_login(_sid(user), user.id, employee_id, body)


@router.delete("/{employee_id}/login", response_model=EmployeeOut)
async def revoke_staff_login(employee_id: UUID, user: Admin, db: Db):
    return await EmployeeService(db).revoke_login(_sid(user), user.id, employee_id)
