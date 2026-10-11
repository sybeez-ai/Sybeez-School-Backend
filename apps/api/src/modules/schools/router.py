from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.audit.repository import AuditRepository
from src.modules.fees.tenant import bind_tenant
from src.modules.finance.service import FinanceService
from src.modules.schools.schemas import BranchList, SchoolProfile, SchoolProfileUpdate
from src.modules.schools.service import SchoolsService
from src.shared.enums import UserRole

router = APIRouter(prefix="/schools", tags=["schools"])


@router.get("/me", response_model=SchoolProfile)
async def my_school(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    return await SchoolsService(db).get_my_school(user.school_id)


@router.get("/me/branches", response_model=BranchList)
async def my_school_branches(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    """Branches of the caller's school.

    Payroll clients read `branches[].name` (holidays, weekly offs, staff profiles).
    Finance campus rows are included with id and code so the fee desk can send
    X-Branch-Id. Cashiers see only their assigned finance branches.
    """
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id)
    items: list[dict] = []
    seen: set[str] = set()
    if user.id is not None:
        svc = await FinanceService(db, user.school_id, user.id, user.role.value).prepare()
        for branch in await svc.list_branches():
            payload = branch.model_dump()
            items.append(payload)
            seen.add(branch.name.strip().lower())
    if user.role != UserRole.CASHIER:
        for name in await SchoolsService(db).list_branches(user.school_id):
            key = name.strip().lower()
            if key not in seen:
                items.append({"name": name})
                seen.add(key)
    return BranchList(branches=items)


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
