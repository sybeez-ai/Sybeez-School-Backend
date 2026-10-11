from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import effective_permissions, require_permission
from src.modules.access.schemas import (
    EffectivePermissionsOut,
    InvitationIn,
    InvitationOut,
    MatrixOut,
    MembershipOut,
    PermissionChangeIn,
    RoleChangeIn,
    RoleGrantIn,
)
from src.modules.access.service import AccessService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/access", tags=["access"])


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("/me", response_model=EffectivePermissionsOut)
async def my_permissions(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("session.read"))],
):
    perms = await effective_permissions(db, user)
    return EffectivePermissionsOut(
        user_id=user.id,
        membership_id=user.membership_id,
        role=user.role.value,
        school_id=user.school_id,
        permissions=sorted(perms),
    )


@router.get("/matrix", response_model=MatrixOut)
async def matrix(
    _: Annotated[CurrentUser, Depends(require_permission("access.read"))],
):
    return AccessService.matrix()


@router.get("/permissions", response_model=dict[str, str])
async def permission_catalog(
    _: Annotated[CurrentUser, Depends(require_permission("access.read"))],
):
    return AccessService.matrix()["permissions"]


@router.get("/memberships", response_model=Page[MembershipOut])
async def list_memberships(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.read"))],
    page: PageQuery,
):
    items, total = await AccessService(db).list_memberships(_school(user), page)
    return Page(items=[MembershipOut(**item) for item in items], total=total, page=page.page, page_size=page.page_size)


@router.get("/memberships/{membership_id}/permissions", response_model=EffectivePermissionsOut)
async def membership_permissions(
    membership_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.read"))],
):
    membership = await AccessService(db)._membership(_school(user), membership_id)
    perms = await AccessService(db).permissions_for_membership(_school(user), membership_id)
    return EffectivePermissionsOut(
        user_id=membership.user_id,
        membership_id=membership.id,
        role=membership.role,
        school_id=membership.school_id,
        permissions=sorted(perms),
    )


@router.post("/invitations", response_model=InvitationOut, status_code=201)
async def invite(
    body: InvitationIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.invite"))],
):
    inv, raw = await AccessService(db).invite(user, _school(user), body)
    out = InvitationOut.model_validate(inv)
    out.invite_token = raw
    return out


@router.get("/invitations", response_model=Page[InvitationOut])
async def list_invitations(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.read"))],
    page: PageQuery,
):
    items, total = await AccessService(db).list_invitations(_school(user), page)
    return Page(
        items=[InvitationOut.model_validate(item) for item in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/invitations/{invitation_id}/revoke", response_model=InvitationOut)
async def revoke_invitation(
    invitation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.revoke"))],
):
    inv = await AccessService(db).revoke_invitation(user, _school(user), invitation_id)
    return InvitationOut.model_validate(inv)


@router.post("/role-grants", response_model=MembershipOut, status_code=201)
async def grant_role(
    body: RoleGrantIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.grant"))],
):
    membership = await AccessService(db).grant_role(user, _school(user), body)
    return MembershipOut(
        id=membership.id,
        user_id=membership.user_id,
        school_id=membership.school_id,
        role=membership.role,
        status=membership.status,
        email=body.email.lower(),
        full_name=body.full_name,
    )


@router.post("/memberships/{membership_id}/role", response_model=MembershipOut)
async def change_role(
    membership_id: UUID,
    body: RoleChangeIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.grant"))],
):
    membership = await AccessService(db).change_role(user, _school(user), membership_id, body)
    return MembershipOut(
        id=membership.id,
        user_id=membership.user_id,
        school_id=membership.school_id,
        role=membership.role,
        status=membership.status,
    )


@router.post("/memberships/{membership_id}/revoke", response_model=MembershipOut)
async def revoke_membership(
    membership_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.revoke"))],
):
    membership = await AccessService(db).revoke_membership(user, _school(user), membership_id)
    return MembershipOut(
        id=membership.id,
        user_id=membership.user_id,
        school_id=membership.school_id,
        role=membership.role,
        status=membership.status,
    )


@router.put("/memberships/{membership_id}/permissions", status_code=200)
async def set_permission(
    membership_id: UUID,
    body: PermissionChangeIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.grant"))],
):
    if body.effect == "REVOKE" and "access.revoke" not in await effective_permissions(db, user):
        raise ForbiddenError("Missing permission: access.revoke")
    row = await AccessService(db).set_override(user, _school(user), membership_id, body)
    return {"id": str(row.id), "permission": row.permission_code, "effect": row.effect}


@router.delete("/memberships/{membership_id}/permissions/{permission_code}", status_code=204)
async def clear_permission(
    membership_id: UUID,
    permission_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("access.revoke"))],
):
    await AccessService(db).clear_override(user, _school(user), membership_id, permission_code)
