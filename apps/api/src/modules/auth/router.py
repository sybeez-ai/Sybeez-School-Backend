import json
import secrets
import urllib.parse
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.config import get_settings
from src.core.cookies import clear_auth_cookies, set_auth_cookies
from src.core.database import get_db
from src.modules.access.deps import public
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
    MembershipOut,
    MessageOut,
    RefreshRequest,
    SwitchSchoolRequest,
    TokenResponse,
    UpdateProfileRequest,
)
from src.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _attach(response: Response, token: TokenResponse) -> TokenResponse:
    if not token.access_token or not token.refresh_token:
        return token
    set_auth_cookies(
        response,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        access_max_age=token.access_expires_in or 0,
        refresh_max_age=token.refresh_expires_in or 0,
    )
    return token


@router.post("/login", response_model=TokenResponse)
@public
async def login(body: LoginRequest, response: Response, db: Annotated[AsyncSession, Depends(get_db)]):
    return _attach(response, await AuthService(db).login(body))


@router.post("/complete-new-password", response_model=TokenResponse)
@public
async def complete_new_password(
    body: CompleteNewPasswordRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return _attach(response, await AuthService(db).complete_new_password(body))


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


@router.post("/logout", response_model=MessageOut)
@public
async def logout(
    response: Response,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: LogoutRequest | None = None,
):
    settings = get_settings()
    raw = body.refresh_token if body is not None and body.refresh_token else request.cookies.get(settings.refresh_cookie_name)
    await AuthService(db).logout(raw)
    clear_auth_cookies(response)
    return MessageOut(message="Signed out")


@router.post("/forgot-password", response_model=MessageOut)
@public
async def forgot_password(body: ForgotPasswordRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    await AuthService(db).forgot_password(body.email)
    return MessageOut(message="If an account exists, a reset code has been sent")


@router.post("/confirm-forgot-password", response_model=MessageOut)
@public
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


@router.get("/memberships", response_model=list[MembershipOut])
async def list_memberships(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Schools and roles this user can sign into, including accountant, cashier, and auditor."""
    return await AuthService(db).list_memberships(user.id)


@router.post("/switch-school", response_model=TokenResponse)
async def switch_school(
    body: SwitchSchoolRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Issue a new access token for another school membership. Role is stamped on the token."""
    return _attach(response, await AuthService(db).switch_school(user.id, body.school_id, body.role))


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
        portals=sorted(user.portals),
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


_HANDOFF_TTL = 60


def safe_next_path(raw: str | None) -> str:
    """Only same-site relative paths: blocks open redirects like //evil.com or /\\evil.com."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw or "\n" in raw or "\r" in raw:
        return "/"
    return raw


@router.post("/web-session/code")
async def web_session_code(user: Annotated[CurrentUser, Depends(get_current_user)]):
    """Mobile app: exchange the Bearer session for a one-time, 60s code that a WebView redeems
    at GET /auth/web-session to receive the web portal's httpOnly cookies (no token in the URL)."""
    code = secrets.token_urlsafe(32)
    r = await get_redis()
    await r.setex(
        f"web_handoff:{code}",
        _HANDOFF_TTL,
        json.dumps(
            {"user_id": str(user.id), "membership_id": str(user.membership_id) if user.membership_id else None},
            separators=(",", ":"),
        ),
    )
    return {"code": code, "expires_in": _HANDOFF_TTL, "redeem_path": "/api/v1/auth/web-session"}


@router.get("/web-session")
@public
async def web_session_redeem(
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str = Query(..., min_length=16, max_length=128),
    next: str | None = Query(default=None),
):
    """WebView lands here with a one-time code; sets auth cookies and redirects into the portal."""
    from uuid import UUID

    r = await get_redis()
    key = f"web_handoff:{code}"
    raw = await r.get(key)
    if not raw:
        raise UnauthorizedError("Sign-in link expired. Please try again from the app.")
    await r.delete(key)
    meta = json.loads(raw)
    svc = AuthService(db)
    user = await svc.repo.get_user(UUID(meta["user_id"]))
    if user is None:
        raise UnauthorizedError("Account not found")
    membership = UUID(meta["membership_id"]) if meta.get("membership_id") else None
    token = await svc._issue_session_for_user(user, membership_id=membership)
    await db.commit()
    if not token.access_token:
        raise UnauthorizedError("Could not start a web session")
    settings = get_settings()
    response = RedirectResponse(
        url=f"{settings.web_app_url.rstrip('/')}{safe_next_path(next)}", status_code=302
    )
    _attach(response, token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/cognito/google/start")
@public
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
@public
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
@public
async def cognito_result(state: str = Query(...)):
    """Frontend polls this once to fetch the issued Sybeez JWT after OAuth."""
    r = await get_redis()
    raw = await r.get(f"oauth_result:{state}")
    if not raw:
        raise NotFoundError("OAuth result not found")
    await r.delete(f"oauth_result:{state}")
    data = json.loads(raw)
    return TokenResponse(**data)
