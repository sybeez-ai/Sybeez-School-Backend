from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.access.deps import require_permission
from src.core.database import get_db
from src.modules.users.schemas import SchoolUserOut, UserStatusUpdate
from src.modules.users.service import UsersService
from src.shared.enums import UserRole

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/health")
async def users_health(_: Annotated[CurrentUser, Depends(require_permission("users.read"))]):
    return {"module": "users", "status": "ok"}


@router.patch(
    "/{user_id}/status",
    response_model=SchoolUserOut,
)
async def set_user_status(
    user_id: UUID,
    body: UserStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN)),
    ],
):
    """Activate or deactivate a school user (also enables/disables Cognito login)."""
    return await UsersService(db).set_active(actor, user_id, is_active=body.is_active)
