from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission
from src.core.database import get_db
from src.modules.platform.schemas import (
    InvitationCreate,
    InvitationOut,
    PlatformDashboard,
    SchoolCreate,
    SchoolOut,
    SchoolUpdate,
)
from src.modules.platform.service import PlatformService
from src.shared.pagination import Page

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/dashboard", response_model=PlatformDashboard)
async def dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_permission("platform.manage"))],
):
    counts = await PlatformService(db).dashboard()
    return PlatformDashboard(**counts)


@router.get("/schools", response_model=Page[SchoolOut])
async def list_schools(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_permission("platform.manage"))],
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
    _: Annotated[CurrentUser, Depends(require_permission("platform.manage"))],
):
    return await PlatformService(db).create_school(body)


@router.get("/schools/{school_id}", response_model=SchoolOut)
async def get_school(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_permission("platform.manage"))],
):
    school = await PlatformService(db).get_school(school_id)
    return SchoolOut.model_validate(school)


@router.patch("/schools/{school_id}", response_model=SchoolOut)
async def update_school(
    school_id: UUID,
    body: SchoolUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_permission("platform.manage"))],
):
    school = await PlatformService(db).update_school(school_id, body)
    return SchoolOut.model_validate(school)


@router.post("/schools/{school_id}/invitations", response_model=InvitationOut, status_code=201)
async def invite_admin(
    school_id: UUID,
    body: InvitationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("platform.manage"))],
):
    inv, raw = await PlatformService(db).invite_school_admin(school_id, body, user.id)
    out = InvitationOut.model_validate(inv)
    out.invite_token = raw
    return out


@router.get("/schools/{school_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_permission("platform.manage"))],
):
    return await PlatformService(db).repo.list_invitations(school_id)
