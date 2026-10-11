from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.modules.access.deps import public, require_permission
from src.modules.auth.schemas import AcceptInviteRequest, LoginRequest, MeResponse, RefreshRequest, TokenResponse
from src.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@public
async def login(body: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    return await AuthService(db).login(body)


@router.post("/refresh", response_model=TokenResponse)
@public
async def refresh(body: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    return await AuthService(db).refresh(body.refresh_token)


@router.post("/accept-invitation", response_model=TokenResponse)
@public
async def accept_invitation(body: AcceptInviteRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    return await AuthService(db).accept_invitation(body)


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[CurrentUser, Depends(require_permission("session.read"))]):
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        school_id=user.school_id,
        is_platform_admin=user.is_platform_admin,
    )
