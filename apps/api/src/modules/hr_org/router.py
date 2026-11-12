from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr_org.schemas import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    DesignationCreate,
    DesignationOut,
    DesignationUpdate,
)
from src.modules.hr_org.service import DepartmentService, DesignationService

router = APIRouter(prefix="/hr", tags=["hr-org"])

Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]
Q = Annotated[str | None, Query(max_length=200)]
Active = Annotated[bool | None, Query()]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


# ── Departments ─────────────────────────────────────────────


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(user: Admin, db: Db, q: Q = None, active: Active = None):
    return await DepartmentService(db).list(_sid(user), q=q, active=active)


@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(body: DepartmentCreate, user: Admin, db: Db):
    return await DepartmentService(db).create(_sid(user), body)


@router.get("/departments/{department_id}", response_model=DepartmentOut)
async def get_department(department_id: UUID, user: Admin, db: Db):
    return await DepartmentService(db).get(_sid(user), department_id)


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
async def update_department(department_id: UUID, body: DepartmentUpdate, user: Admin, db: Db):
    return await DepartmentService(db).update(_sid(user), department_id, body)


@router.delete("/departments/{department_id}", status_code=204)
async def delete_department(department_id: UUID, user: Admin, db: Db):
    await DepartmentService(db).delete(_sid(user), department_id)
    return Response(status_code=204)


# ── Designations ────────────────────────────────────────────


@router.get("/designations", response_model=list[DesignationOut])
async def list_designations(user: Admin, db: Db, q: Q = None, active: Active = None):
    return await DesignationService(db).list(_sid(user), q=q, active=active)


@router.post("/designations", response_model=DesignationOut, status_code=201)
async def create_designation(body: DesignationCreate, user: Admin, db: Db):
    return await DesignationService(db).create(_sid(user), body)


@router.get("/designations/{designation_id}", response_model=DesignationOut)
async def get_designation(designation_id: UUID, user: Admin, db: Db):
    return await DesignationService(db).get(_sid(user), designation_id)


@router.patch("/designations/{designation_id}", response_model=DesignationOut)
async def update_designation(designation_id: UUID, body: DesignationUpdate, user: Admin, db: Db):
    return await DesignationService(db).update(_sid(user), designation_id, body)


@router.delete("/designations/{designation_id}", status_code=204)
async def delete_designation(designation_id: UUID, user: Admin, db: Db):
    await DesignationService(db).delete(_sid(user), designation_id)
    return Response(status_code=204)
