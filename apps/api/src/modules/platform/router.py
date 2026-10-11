from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_super_admin
from src.core.database import get_db
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from src.db.models import AuditLog
from src.modules.platform.schemas import (
    InvitationCreate,
    InvitationOut,
    PlatformDashboard,
    SchoolCreate,
    SchoolOut,
    SchoolUpdate,
)
from src.modules.platform.service import PlatformService
from src.modules.platform.staff_service import StaffService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/dashboard", response_model=PlatformDashboard)
async def dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
):
    counts = await PlatformService(db).dashboard()
    return PlatformDashboard(**counts)


@router.get("/schools", response_model=Page[SchoolOut])
async def list_schools(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    items, total = await PlatformService(db).list_schools(page, page_size)
    outs = [SchoolOut.model_validate(s) for s in items]
    return Page(items=outs, total=total, page=page, page_size=page_size)


@router.post("/schools", response_model=SchoolOut, status_code=201)
async def create_school(
    body: SchoolCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
):
    return await PlatformService(db).create_school(body)


@router.get("/schools/{school_id}", response_model=SchoolOut)
async def get_school(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
):
    school = await PlatformService(db).get_school(school_id)
    return SchoolOut.model_validate(school)


@router.patch("/schools/{school_id}", response_model=SchoolOut)
async def update_school(
    school_id: UUID,
    body: SchoolUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
):
    school = await PlatformService(db).update_school(school_id, body)
    return SchoolOut.model_validate(school)


@router.post("/schools/{school_id}/invitations", response_model=InvitationOut, status_code=201)
async def invite_admin(
    school_id: UUID,
    body: InvitationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin())],
):
    inv, raw = await PlatformService(db).invite_school_admin(school_id, body, user.id)
    out = InvitationOut.model_validate(inv)
    out.invite_token = raw
    return out


@router.get("/schools/{school_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
):
    return await PlatformService(db).repo.list_invitations(school_id)


class StaffCreateIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    portals: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class StaffAccessIn(BaseModel):
    portals: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class StaffInviteIn(BaseModel):
    email: EmailStr
    portals: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    expires_in_days: int = Field(default=7, ge=1, le=30)


class StaffOut(BaseModel):
    id: UUID
    user_id: UUID
    email: str
    full_name: str
    status: str
    portals: list[str]
    permissions: list[str]


def _staff_out(row: dict) -> StaffOut:
    return StaffOut(**row)


@router.get("/staff", response_model=list[StaffOut])
async def list_staff(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
):
    return [_staff_out(row) for row in await StaffService(db).list_staff()]


@router.post("/staff", response_model=StaffOut, status_code=201)
async def create_staff(
    body: StaffCreateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin())],
):
    staff = await StaffService(db).create(
        user,
        email=body.email,
        full_name=body.full_name,
        password=body.password,
        portals=body.portals,
        permissions=body.permissions,
    )
    rows = await StaffService(db).list_staff()
    match = next(row for row in rows if row["id"] == staff.id)
    return _staff_out(match)


@router.put("/staff/{staff_id}/access", response_model=StaffOut)
async def set_staff_access(
    staff_id: UUID,
    body: StaffAccessIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin())],
):
    await StaffService(db).set_access(user, staff_id, portals=body.portals, permissions=body.permissions)
    rows = await StaffService(db).list_staff()
    return _staff_out(next(row for row in rows if row["id"] == staff_id))


@router.post("/staff/{staff_id}/revoke", response_model=StaffOut)
async def revoke_staff(
    staff_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin())],
):
    await StaffService(db).revoke(user, staff_id)
    rows = await StaffService(db).list_staff()
    return _staff_out(next(row for row in rows if row["id"] == staff_id))


@router.post("/staff-invitations", status_code=201)
async def invite_staff(
    body: StaffInviteIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin())],
):
    inv, raw = await StaffService(db).invite(
        user,
        email=body.email,
        portals=body.portals,
        permissions=body.permissions,
        expires_in_days=body.expires_in_days,
    )
    return {
        "id": str(inv.id),
        "email": inv.email,
        "status": inv.status,
        "expires_at": inv.expires_at.isoformat(),
        "invite_token": raw,
    }


@router.get("/audit")
async def platform_audit(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin())],
    page: PageQuery,
    action: str | None = None,
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(page.offset).limit(page.page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(row.id),
                "action": row.action,
                "message": row.message,
                "actor_id": str(row.actor_id) if row.actor_id else None,
                "school_id": str(row.school_id) if row.school_id else None,
                "details": row.details,
            }
            for row in rows
        ],
        "page": page.page,
        "page_size": page.page_size,
    }
