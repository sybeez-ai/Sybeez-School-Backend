import json
import secrets
import urllib.parse
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.config import get_settings
from src.core.database import get_db
from src.core.exceptions import NotFoundError, UnauthorizedError
from src.core.redis import get_redis
from src.modules.auth import cognito as cognito_svc
from src.modules.auth.schemas import (
    AcceptInviteRequest,
    CompleteNewPasswordRequest,
    ConfirmForgotPasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    MessageOut,
    RefreshRequest,
    TokenResponse,
    UpdateProfileRequest,
)
from src.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    return await AuthService(db).login(body)


@router.post("/complete-new-password", response_model=TokenResponse)
async def complete_new_password(
    body: CompleteNewPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await AuthService(db).complete_new_password(body)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    return await AuthService(db).refresh(body.refresh_token)


@router.post("/logout", response_model=MessageOut)
async def logout(body: LogoutRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    await AuthService(db).logout(body.refresh_token)
    return MessageOut(message="Signed out")


@router.post("/forgot-password", response_model=MessageOut)
async def forgot_password(body: ForgotPasswordRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    await AuthService(db).forgot_password(body.email)
    return MessageOut(message="If an account exists, a reset code has been sent")


@router.post("/confirm-forgot-password", response_model=MessageOut)
async def confirm_forgot_password(
    body: ConfirmForgotPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await AuthService(db).confirm_forgot_password(
        email=body.email,
        confirmation_code=body.confirmation_code,
        new_password=body.new_password,
    )
    return MessageOut(message="Password updated. You can sign in now.")


@router.post("/accept-invitation", response_model=TokenResponse)
async def accept_invitation(body: AcceptInviteRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    return await AuthService(db).accept_invitation(body)


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from sqlalchemy import select

    from src.db.models import User

    row = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        school_id=user.school_id,
        is_platform_admin=user.is_platform_admin,
        platform_role=user.platform_role,
        is_active=bool(row.is_active),
    )


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateProfileRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    updated = await AuthService(db).update_profile(user.id, full_name=body.full_name)
    return MeResponse(
        id=updated.id,
        email=updated.email,
        full_name=updated.full_name,
        role=user.role.value,
        school_id=user.school_id,
        is_platform_admin=user.is_platform_admin,
        platform_role=user.platform_role,
        is_active=bool(updated.is_active),
    )


@router.get("/cognito/google/start")
async def cognito_google_start(
    next: str | None = Query(default=None),
):
    """Redirect browser to Cognito Hosted UI Google login."""
    state = secrets.token_urlsafe(24)
    r = await get_redis()
    await r.setex(f"oauth_state:{state}", 300, json.dumps({"next": next or "/"}, separators=(",", ":")))
    url = cognito_svc.hosted_ui_google_authorize_url(state=state)
    return RedirectResponse(url=url, status_code=302)


@router.get("/cognito/callback")
async def cognito_callback(
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str = Query(...),
    state: str = Query(...),
):
    """Cognito redirects here after Google sign-in. Exchanges code, issues Sybeez JWT, stores result in Redis, then redirects to web."""
    if not code or not state:
        raise UnauthorizedError("Missing OAuth code/state")
    r = await get_redis()
    raw = await r.get(f"oauth_state:{state}")
    if not raw:
        raise UnauthorizedError("OAuth state expired")
    meta = json.loads(raw)
    next_path = meta.get("next") or "/"
    await r.delete(f"oauth_state:{state}")

    cog = await anyio.to_thread.run_sync(cognito_svc.exchange_oauth_code_for_tokens, code=code)
    # Best-effort: link Google identity to existing password user (same email)
    if cog.email:
        cognito_svc.admin_link_google_to_user_by_email(email=cog.email, id_token=cog.id_token)

    svc = AuthService(db)
    user = await svc._user_from_cognito(cog, cog.email or "")
    token = await svc._issue_session_for_user(user)

    await r.setex(f"oauth_result:{state}", 120, token.model_dump_json())
    settings = get_settings()
    redirect = (
        f"{settings.web_app_url.rstrip('/')}/oauth/cognito?state={urllib.parse.quote(state)}"
        f"&next={urllib.parse.quote(next_path)}"
    )
    return RedirectResponse(url=redirect, status_code=302)


@router.get("/cognito/result", response_model=TokenResponse)
async def cognito_result(state: str = Query(...)):
    """Frontend polls this once to fetch the issued Sybeez JWT after OAuth."""
    r = await get_redis()
    raw = await r.get(f"oauth_result:{state}")
    if not raw:
        raise NotFoundError("OAuth result not found")
    await r.delete(f"oauth_result:{state}")
    data = json.loads(raw)
    return TokenResponse(**data)
