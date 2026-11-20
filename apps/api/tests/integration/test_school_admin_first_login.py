"""School admin created in the platform console: temporary password, new password, then normal login."""

import base64
import json
from uuid import uuid4

from src.core.security import create_access_token
from src.db.models import User
from src.modules.auth.cognito import CognitoAuthResult, CognitoNewPasswordChallenge

LOGIN = "/api/v1/auth/login"
NEW = "My-Own-Pass-456!"


def _jwt(email: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "cognito-sub-1", "email": email}).encode()).decode().rstrip("=")
    return f"e30.{payload}.e30"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_then_first_login_then_new_password_then_relogin(api, db_session, hr_world, monkeypatch):
    plat = await db_session.get(User, hr_world.admin_b.user_id)
    plat.is_platform_admin = True
    plat.platform_role = "SUPER_ADMIN"
    await db_session.flush()
    token = create_access_token(
        subject=plat.id,
        role="PLATFORM_ADMIN",
        school_id=None,
        extra={"platform_role": "SUPER_ADMIN"},
    )
    email = f"school-admin-{uuid4().hex[:8]}@example.com"
    accounts: dict[str, dict] = {}

    def configured() -> bool:
        return True

    def _row(address: str) -> dict:
        return accounts.setdefault(
            address.strip().lower(),
            {"temp": None, "permanent": None, "username": "cognito-user"},
        )

    def admin_create_user(*, email, temporary_password, full_name, make_permanent=False):
        accounts[email.strip().lower()] = {
            "temp": temporary_password,
            "permanent": None,
            "username": "cognito-user",
        }
        return "cognito-sub-1"

    def authenticate(address, password):
        row = accounts.get(address.strip().lower())
        from src.core.exceptions import UnauthorizedError

        if row is None or (row["permanent"] is None and password != row["temp"]) or (
            row["permanent"] is not None and password != row["permanent"]
        ):
            raise UnauthorizedError("Invalid email or password")
        if row["permanent"] is None:
            return CognitoNewPasswordChallenge(
                session="session-cognito-1", email=address, username=row["username"]
            )
        return CognitoAuthResult(
            access_token="access",
            id_token=_jwt(address),
            refresh_token="refresh",
            sub="cognito-sub-1",
            email=address,
        )

    def complete_new_password(*, email, session, new_password, full_name=None, username=None):
        assert session == "session-cognito-1"
        assert username in (None, "", "cognito-user")
        row = _row(email)
        row["permanent"] = new_password
        row["temp"] = None
        return authenticate(email, new_password)

    def admin_set_temporary_password(*, email, temporary_password):
        accounts[email.strip().lower()] = {
            "temp": temporary_password,
            "permanent": None,
            "username": "cognito-user",
        }

    import src.modules.auth.cognito as cognito

    monkeypatch.setattr(cognito, "cognito_configured", configured)
    monkeypatch.setattr(cognito, "admin_create_user", admin_create_user)
    monkeypatch.setattr(cognito, "authenticate", authenticate)
    monkeypatch.setattr(cognito, "complete_new_password", complete_new_password)
    monkeypatch.setattr(cognito, "admin_set_temporary_password", admin_set_temporary_password)
    monkeypatch.setattr(cognito, "sync_user_groups", lambda **kwargs: True)
    monkeypatch.setattr(cognito, "sync_school_identity", lambda **kwargs: None)

    created = await api.post(
        f"/api/v1/platform/schools/{hr_world.admin_a.school_id}/admins",
        json={"full_name": "New School Admin", "email": email, "password": "Temp-Admin-123!"},
        headers=_h(token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["temporary_password"] == "Temp-Admin-123!"
    assert created.json()["login_url"] == "/school-admin/login"

    first = await api.post(LOGIN, json={"email": email, "password": "Temp-Admin-123!", "portal": "school"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["challenge"] == "NEW_PASSWORD_REQUIRED" and body["session"]
    assert body.get("access_token") in (None, "")

    done = await api.post(
        "/api/v1/auth/complete-new-password",
        json={"email": email, "session": body["session"], "new_password": NEW, "portal": "school"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["role"] == "SCHOOL_ADMIN"
    assert done.json()["school_id"] == str(hr_world.admin_a.school_id)
    assert done.json()["must_change_password"] is False

    again = await api.post(LOGIN, json={"email": email, "password": NEW, "portal": "school"})
    assert again.status_code == 200, again.text
    assert again.json()["role"] == "SCHOOL_ADMIN"
    assert again.json().get("challenge") in (None, "")
    assert (await api.post(LOGIN, json={"email": email, "password": "Temp-Admin-123!", "portal": "school"})).status_code == 401

    home = await api.get("/api/v1/admin/dashboard", headers=_h(again.json()["access_token"]))
    assert home.status_code == 200, home.text

    reset = await api.post(
        f"/api/v1/platform/schools/{hr_world.admin_a.school_id}/admin/reset-password",
        json={},
        headers=_h(token),
    )
    assert reset.status_code == 200, reset.text
    from sqlalchemy import func

    reset_email = reset.json()["admin_email"].strip().lower()
    second_temp = reset.json()["temporary_password"]
    assert second_temp and second_temp != NEW
    reset_user = (
        await db_session.execute(select(User).where(func.lower(User.email) == reset_email))
    ).scalar_one()
    await db_session.refresh(reset_user)
    assert reset_user.must_change_password is True
    if reset_user.cognito_sub:
        assert reset_user.password_hash is None

    again_reset = await api.post(
        f"/api/v1/platform/schools/{hr_world.admin_a.school_id}/admin/reset-password",
        json={},
        headers=_h(token),
    )
    assert again_reset.status_code == 200, again_reset.text
    third_temp = again_reset.json()["temporary_password"]
    assert third_temp != second_temp
    await db_session.refresh(reset_user)
    assert reset_user.must_change_password is True
