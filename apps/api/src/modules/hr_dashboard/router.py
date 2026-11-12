from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr_dashboard.schemas import HrDashboardOut
from src.modules.hr_dashboard.service import HrDashboardService

router = APIRouter(prefix="/hr", tags=["hr-dashboard"])

Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/dashboard", response_model=HrDashboardOut)
async def hr_dashboard(user: Admin, db: Db, as_of: date | None = None):
    return await HrDashboardService(db).overview(_sid(user), as_of)
