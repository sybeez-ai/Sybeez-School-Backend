from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr_settings.schemas import HrSettingsOut, HrSettingsUpdate
from src.modules.hr_settings.service import HrSettingsService

router = APIRouter(prefix="/hr", tags=["hr-settings"])

Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/settings", response_model=HrSettingsOut)
async def get_hr_settings(user: Admin, db: Db):
    return await HrSettingsService(db).get(_sid(user))


@router.patch("/settings", response_model=HrSettingsOut)
async def update_hr_settings(body: HrSettingsUpdate, user: Admin, db: Db):
    return await HrSettingsService(db).update(_sid(user), user.id, body)
