"""Mobile (bearer, no cookies) use of forgot-password, accept-invitation and complete-new-password."""

from datetime import timedelta
from uuid import uuid4

from src.db.models import Invitation, User
from src.modules.auth import cognito as cognito_svc
from src.modules.auth.repository import hash_token
from src.shared.utils import utcnow

PREFIX = "/api/v1/auth"


async def test_accept_invitation_returns_bearer_tokens_that_work_without_cookies(api, db_session, hr_world):
    raw = f"inv-{uuid4().hex}"
    email = f"invitee-{uuid4().hex[:8]}@example.com"
    db_session.add(
        Invitation(
            school_id=hr_world.admin_a.school_id,
            email=email,
            role="PARENT",
            token_hash=hash_token(raw),
            status="PENDING",
            expires_at=utcnow() + timedelta(days=2),
        )
    )
    await db_session.flush()
    r = await api.post(f"{PREFIX}/accept-invitation", json={"token": raw, "password": "Invite-Pass-123!", "full_name": "New Parent"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"] and body["role"] == "PARENT"
    api.cookies.clear()
    me = await api.get(f"{PREFIX}/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == email
    again = await api.post(f"{PREFIX}/accept-invitation", json={"token": raw, "password": "Invite-Pass-123!", "full_name": "New Parent"})
    assert again.status_code == 409
    bad = await api.post(f"{PREFIX}/accept-invitation", json={"token": "nope", "password": "Invite-Pass-123!", "full_name": "X Y"})
    assert bad.status_code == 404


async def test_expired_invitation_is_refused(api, db_session, hr_world):
    raw = f"inv-{uuid4().hex}"
    db_session.add(
        Invitation(
            school_id=hr_world.admin_a.school_id, email=f"old-{uuid4().hex[:6]}@example.com", role="TEACHER",
            token_hash=hash_token(raw), status="PENDING", expires_at=utcnow() - timedelta(days=1),
        )
    )
    await db_session.flush()
    r = await api.post(f"{PREFIX}/accept-invitation", json={"token": raw, "password": "Invite-Pass-123!", "full_name": "Old Invite"})
    assert r.status_code == 403


async def test_forgot_password_never_reveals_whether_the_account_exists(api, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(cognito_svc, "cognito_configured", lambda: True)
    monkeypatch.setattr(cognito_svc, "admin_forgot_password", lambda *, email: sent.append(email))
    for email in ("known@example.com", "nobody@example.com"):
        r = await api.post(f"{PREFIX}/forgot-password", json={"email": email})
        assert r.status_code == 200 and "If an account exists" in r.json()["message"]
    assert sent == ["known@example.com", "nobody@example.com"]


async def test_confirm_forgot_password_resets_and_revokes_sessions(api, db_session, hr_world, monkeypatch):
    actor = hr_world.student_a
    user = await db_session.get(User, actor.user_id)
    user.email = f"fp-{uuid4().hex[:8]}@example.com"
    user.password_hash = "x"
    await db_session.flush()
    seen: dict = {}
    monkeypatch.setattr(cognito_svc, "cognito_configured", lambda: True)
    monkeypatch.setattr(cognito_svc, "admin_confirm_forgot_password", lambda **kw: seen.update(kw))
    r = await api.post(
        f"{PREFIX}/confirm-forgot-password",
        json={"email": user.email, "confirmation_code": "123456", "new_password": "Brand-New-Pass-1!"},
    )
    assert r.status_code == 200, r.text
    assert seen["confirmation_code"] == "123456"
    await db_session.refresh(user)
    assert user.password_hash is None


async def test_complete_new_password_issues_bearer_session(api, db_session, hr_world, monkeypatch):
    actor = hr_world.student_a
    user = await db_session.get(User, actor.user_id)
    user.email = f"np-{uuid4().hex[:8]}@example.com"
    await db_session.flush()
    monkeypatch.setattr(
        cognito_svc,
        "complete_new_password",
        lambda **kw: cognito_svc.CognitoAuthResult("a", "i", "r", f"sub-{uuid4().hex}", kw["email"]),
    )
    r = await api.post(
        f"{PREFIX}/complete-new-password",
        json={"email": user.email, "session": "s" * 20, "new_password": "Brand-New-Pass-1!", "portal": "student"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] and r.json()["must_change_password"] is False
