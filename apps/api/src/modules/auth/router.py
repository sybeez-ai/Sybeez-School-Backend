from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.cookies import clear_auth_cookies, set_auth_cookies
from src.core.database import get_db
from src.core.exceptions import UnauthorizedError
from src.modules.access.deps import public, require_permission
from src.modules.auth.schemas import AcceptInviteRequest, LoginRequest, MeResponse, RefreshRequest, TokenResponse
from src.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _attach(response: Response, token: TokenResponse) -> TokenResponse:
    set_auth_cookies(
        response,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        access_max_age=token.access_expires_in,
        refresh_max_age=token.refresh_expires_in,
    )
    return token


@router.post("/login", response_model=TokenResponse)
@public
async def login(body: LoginRequest, response: Response, db: Annotated[AsyncSession, Depends(get_db)]):
    return _attach(response, await AuthService(db).login(body))


@router.post("/refresh", response_model=TokenResponse)
@public
async def refresh(
    response: Response,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: RefreshRequest | None = None,
):
    settings = get_settings()
    raw = None
    if body is not None and body.refresh_token:
        raw = body.refresh_token
    raw = raw or request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise UnauthorizedError("Missing refresh token")
    return _attach(response, await AuthService(db).refresh(raw))


@router.post("/logout")
@public
async def logout(
    response: Response,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: RefreshRequest | None = None,
):
    settings = get_settings()
    raw = body.refresh_token if body is not None and body.refresh_token else request.cookies.get(settings.refresh_cookie_name)
    await AuthService(db).logout(raw)
    clear_auth_cookies(response)
    return {"status": "ok"}


@router.post("/accept-invitation", response_model=TokenResponse)
@public
async def accept_invitation(
    body: AcceptInviteRequest, response: Response, db: Annotated[AsyncSession, Depends(get_db)]
):
    return _attach(response, await AuthService(db).accept_invitation(body))


@router.post("/accept-staff-invitation", response_model=TokenResponse)
@public
async def accept_staff_invitation(
    body: AcceptInviteRequest, response: Response, db: Annotated[AsyncSession, Depends(get_db)]
):
    return _attach(response, await AuthService(db).accept_staff_invitation(body))


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
