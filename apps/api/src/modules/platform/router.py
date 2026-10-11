from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    CurrentUser,
    require_admin_or_above,
    require_platform_member,
    require_roles,
    require_super_admin,
)
from src.core.database import get_db
from src.db.models import School, User
from src.modules.audit.schemas import AuditLogOut
from src.modules.audit.service import AuditService
from src.modules.fees.route_schemas import PlatformRouteUpdate, RouteSchoolOut
from src.modules.fees.route_service import RouteService
from src.modules.platform.saas_service import PlatformSaasService
from src.modules.platform.schemas import (
    InvitationCreate,
    InvitationOut,
    PlanCreate,
    PlanOut,
    PlanUpdate,
    PlatformAiOverview,
    PlatformAiSettingsOut,
    PlatformAiSettingsUpdate,
    PlatformDashboard,
    PlatformEmployeeCreate,
    PlatformEmployeeOut,
    PlatformEmployeeUpdate,
    PlatformSearchResult,
    PlatformSettingsOut,
    PlatformSettingsUpdate,
    SchoolAdminDirectoryOut,
    SchoolAdminMfaRecoveryOut,
    SchoolAdminResetPasswordOut,
    SchoolAdminResetPasswordRequest,
    SchoolCreate,
    SchoolDetailOut,
    SchoolLifecycleRequest,
    SchoolOut,
    SchoolUpdate,
)
from src.modules.platform.service import PlatformService
from src.modules.platform.staff_service import StaffService
from pydantic import BaseModel, EmailStr, Field
from src.shared.enums import PlatformRole, SchoolStatus, UserRole
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/platform", tags=["platform"])


async def _actor_user(db: AsyncSession, actor: CurrentUser) -> User:
    return (await db.execute(select(User).where(User.id == actor.id))).scalar_one()


@router.get("/dashboard", response_model=PlatformDashboard)
async def dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_platform_member)],
):
    counts = await PlatformService(db).dashboard()
    return PlatformDashboard(**counts)


@router.get("/search", response_model=PlatformSearchResult)
async def platform_search(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
    q: str = Query(..., min_length=1, max_length=120),
):
    return await PlatformService(db).global_search(q.strip())


@router.get("/employees", response_model=list[PlatformEmployeeOut])
async def list_employees(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_admin_or_above)],
):
    return await PlatformService(db).list_employees()


@router.get("/employees/{employee_id}", response_model=PlatformEmployeeOut)
async def get_employee(
    employee_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_admin_or_above)],
):
    return await PlatformService(db).get_employee(employee_id)


@router.post("/employees", response_model=PlatformEmployeeOut, status_code=201)
async def create_employee(
    body: PlatformEmployeeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_admin_or_above)],
):
    return await PlatformService(db).create_employee(body, await _actor_user(db, actor))


@router.patch("/employees/{employee_id}", response_model=PlatformEmployeeOut)
async def update_employee(
    employee_id: UUID,
    body: PlatformEmployeeUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_admin_or_above)],
):
    return await PlatformService(db).update_employee(employee_id, body, await _actor_user(db, actor))


@router.get("/audit", response_model=Page[AuditLogOut])
async def platform_audit(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin)],
    page: PageQuery,
    action: str | None = Query(default=None),
):
    items, total = await AuditService(db).list_actions(page, action=action)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get("/schools", response_model=Page[SchoolOut])
async def list_schools(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    plan_code: str | None = Query(default=None),
    country: str | None = Query(default=None),
    sort: str = Query(default="created_at"),
):
    items, total = await PlatformService(db).list_schools(
        user,
        page,
        page_size,
        q=q,
        status=status,
        plan_code=plan_code,
        country=country,
        sort=sort,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/schools", response_model=SchoolOut, status_code=201)
async def create_school(
    body: SchoolCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformService(db).create_school(user, body)


@router.get("/schools/{school_id}", response_model=SchoolDetailOut)
async def get_school(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformService(db).get_school(user, school_id)


@router.patch("/schools/{school_id}", response_model=SchoolOut)
async def update_school(
    school_id: UUID,
    body: SchoolUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformService(db).update_school(user, school_id, body)


@router.post("/schools/{school_id}/suspend", response_model=SchoolOut)
async def suspend_school(
    school_id: UUID,
    body: SchoolLifecycleRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformService(db).set_school_status(
        user, school_id, status=SchoolStatus.SUSPENDED.value, reason=body.reason
    )


@router.post("/schools/{school_id}/reactivate", response_model=SchoolOut)
async def reactivate_school(
    school_id: UUID,
    body: SchoolLifecycleRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformService(db).set_school_status(
        user, school_id, status=SchoolStatus.ACTIVE.value, reason=body.reason
    )


@router.post("/schools/{school_id}/archive", response_model=SchoolOut)
async def archive_school(
    school_id: UUID,
    body: SchoolLifecycleRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformService(db).set_school_status(
        user,
        school_id,
        status=SchoolStatus.ARCHIVED.value,
        reason=body.reason,
        confirm_name=body.confirm_name,
    )


@router.delete("/schools/{school_id}", status_code=204)
async def delete_school(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_super_admin)],
):
    await PlatformService(db).delete_school(actor, school_id)
    return Response(status_code=204)


@router.post("/schools/{school_id}/admin/reset-password", response_model=SchoolAdminResetPasswordOut)
async def reset_school_admin_password(
    school_id: UUID,
    body: SchoolAdminResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    email, temp = await PlatformService(db).reset_school_admin_password(
        actor, school_id, temporary_password=body.temporary_password
    )
    return SchoolAdminResetPasswordOut(admin_email=email, temporary_password=temp)


@router.post("/schools/{school_id}/admin/clear-mfa", response_model=SchoolAdminMfaRecoveryOut)
async def clear_school_admin_mfa(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    email = await PlatformService(db).clear_school_admin_mfa(actor, school_id)
    return SchoolAdminMfaRecoveryOut(admin_email=email)


@router.post("/schools/{school_id}/logo", response_model=SchoolOut)
async def upload_school_logo(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
    file: UploadFile = File(...),
):
    return await PlatformService(db).upload_school_logo(actor, school_id, file)


@router.post("/schools/{school_id}/invitations", response_model=InvitationOut, status_code=201)
async def invite_admin(
    school_id: UUID,
    body: InvitationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin)],
):
    inv, raw = await PlatformService(db).invite_school_admin(user, school_id, body)
    out = InvitationOut.model_validate(inv)
    out.invite_token = raw
    return out


@router.get("/schools/{school_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin)],
):
    return await PlatformService(db).repo.list_invitations(school_id)


@router.get("/admins", response_model=Page[SchoolAdminDirectoryOut])
async def list_school_admins(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    q: str | None = Query(default=None),
    school_id: UUID | None = Query(default=None),
):
    items, total = await PlatformService(db).list_school_admins(
        q=q, school_id=school_id, page=page, page_size=page_size
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/admins/{admin_id}", response_model=list[SchoolAdminDirectoryOut])
async def get_school_admin(
    admin_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformService(db).get_school_admin(admin_id)


@router.get("/activity", response_model=list[AuditLogOut])
async def platform_activity(
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_platform_member)],
    school_id: UUID | None = Query(default=None),
    limit: int = Query(200, ge=1, le=500),
):
    return await AuditService(db).list_platform_activity(
        actor_id=actor.id,
        is_admin=(
            actor.platform_role
            in {
                PlatformRole.SUPER_ADMIN.value,
                PlatformRole.ADMIN.value,
                PlatformRole.PLATFORM_ADMIN.value,
            }
        ),
        school_id=school_id,
        limit=limit,
    )


@router.get("/schools/{school_id}/access", response_model=list[PlatformEmployeeOut])
async def list_school_access(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_admin_or_above)],
):
    return await PlatformService(db).list_school_access(actor, school_id)


@router.post("/schools/{school_id}/access/{user_id}", status_code=204)
async def grant_school_access(
    school_id: UUID,
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_admin_or_above)],
):
    await PlatformService(db).grant_school_access(actor, school_id, user_id)
    return Response(status_code=204)


@router.delete("/schools/{school_id}/access/{user_id}", status_code=204)
async def revoke_school_access(
    school_id: UUID,
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_admin_or_above)],
):
    await PlatformService(db).revoke_school_access(actor, school_id, user_id)
    return Response(status_code=204)


# --- Plans ---


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_platform_member)],
    include_archived: bool = Query(default=False),
):
    return await PlatformSaasService(db).list_plans(include_archived=include_archived)


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(
    body: PlanCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).create_plan(actor, body)


@router.get("/plans/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).get_plan(plan_id)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: UUID,
    body: PlanUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).update_plan(actor, plan_id, body)


@router.post("/plans/{plan_id}/archive", response_model=PlanOut)
async def archive_plan(
    plan_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).archive_plan(actor, plan_id)


# --- Settings ---


@router.get("/settings", response_model=PlatformSettingsOut)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    row = await PlatformSaasService(db).get_settings()
    await db.commit()
    return row


@router.patch("/settings", response_model=PlatformSettingsOut)
async def update_settings(
    body: PlatformSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).update_settings(actor, body)


# --- AI control plane (no provider secrets) ---


@router.get("/ai", response_model=PlatformAiOverview)
async def ai_overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).ai_overview()


@router.get("/ai/settings", response_model=PlatformAiSettingsOut)
async def get_ai_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    row = await PlatformSaasService(db).get_ai_settings()
    await db.commit()
    return row


@router.patch("/ai/settings", response_model=PlatformAiSettingsOut)
async def update_ai_settings(
    body: PlatformAiSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).update_ai_settings(actor, body)


@router.get("/ai/providers")
async def ai_providers(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    settings = await PlatformSaasService(db).get_ai_settings()
    await db.commit()
    # Never expose API keys — only status metadata from settings JSON
    return {
        "default_provider": settings.default_provider,
        "fallback_provider": settings.fallback_provider,
        "providers": settings.providers_json,
    }


@router.get("/ai/models")
async def ai_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    settings = await PlatformSaasService(db).get_ai_settings()
    await db.commit()
    return {
        "default_chat_model": settings.default_chat_model,
        "fallback_chat_model": settings.fallback_chat_model,
        "models": settings.models_json,
    }


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
    _: Annotated[CurrentUser, Depends(require_super_admin)],
):
    return [_staff_out(row) for row in await StaffService(db).list_staff()]


@router.post("/staff", response_model=StaffOut, status_code=201)
async def create_staff(
    body: StaffCreateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin)],
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
    user: Annotated[CurrentUser, Depends(require_super_admin)],
):
    await StaffService(db).set_access(user, staff_id, portals=body.portals, permissions=body.permissions)
    rows = await StaffService(db).list_staff()
    return _staff_out(next(row for row in rows if row["id"] == staff_id))


@router.post("/staff/{staff_id}/revoke", response_model=StaffOut)
async def revoke_staff(
    staff_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin)],
):
    await StaffService(db).revoke(user, staff_id)
    rows = await StaffService(db).list_staff()
    return _staff_out(next(row for row in rows if row["id"] == staff_id))


@router.post("/staff-invitations", status_code=201)
async def invite_staff(
    body: StaffInviteIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin)],
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


@router.get("/route/accounts", response_model=list[RouteSchoolOut])
async def list_route_accounts(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin)],
):
    return await RouteService(db).list_schools()


@router.patch("/schools/{school_id}/route", response_model=RouteSchoolOut)
async def update_school_route(
    school_id: UUID,
    body: PlatformRouteUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_super_admin)],
):
    status = await RouteService(db).update_platform_fees(school_id, user.id, body)
    school = await db.get(School, school_id)
    return RouteSchoolOut(school_name=school.name if school else "", **status.model_dump())


@router.get("/ai/usage", response_model=PlatformAiOverview)
async def ai_usage(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
):
    return await PlatformSaasService(db).ai_overview()
