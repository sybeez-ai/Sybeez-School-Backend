from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.audit.repository import AuditRepository
from src.modules.fees.tenant import bind_tenant
from src.modules.finance.schemas import BranchOut
from src.modules.finance.service import FinanceService
from src.modules.schools.schemas import SchoolProfile, SchoolProfileUpdate
from src.modules.schools.service import SchoolsService
from src.shared.enums import UserRole

router = APIRouter(prefix="/schools", tags=["schools"])


@router.get("/me", response_model=SchoolProfile)
async def my_school(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    return await SchoolsService(db).get_my_school(user.school_id)


@router.get("/me/branches", response_model=list[BranchOut])
async def my_branches(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    """Branches of the caller's school. Cashiers see only assigned branches.

    Send the chosen branch on later fee calls as the X-Branch-Id header.
    """
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id)
    svc = await FinanceService(db, user.school_id, user.id, user.role.value).prepare()
    return await svc.list_branches()


@router.patch("/me", response_model=SchoolProfile)
async def update_my_school(
    body: SchoolProfileUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    if user.role != UserRole.SCHOOL_ADMIN:
        raise ForbiddenError("School admin only")
    school = await SchoolsService(db).update_my_school(user.school_id, name=body.name)
    await AuditRepository(db).write(
        action="school.profile.update",
        actor_id=user.id,
        school_id=school.id,
        resource_type="school",
        resource_id=str(school.id),
        message="School profile updated",
        details={"fields": list(body.model_dump(exclude_unset=True).keys())},
    )
    return school


@router.post("/me/logo", response_model=SchoolProfile)
async def upload_my_school_logo(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
    file: UploadFile = File(...),
):
    if user.role != UserRole.SCHOOL_ADMIN:
        raise ForbiddenError("School admin only")
    school = await SchoolsService(db).upload_my_school_logo(user.school_id, file)
    await AuditRepository(db).write(
        action="school.logo.update",
        actor_id=user.id,
        school_id=school.id,
        resource_type="school",
        resource_id=str(school.id),
        message="School logo updated",
        details={"logo_url": school.logo_url},
    )
    return school
