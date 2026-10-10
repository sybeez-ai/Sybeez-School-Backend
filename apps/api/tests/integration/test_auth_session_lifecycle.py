"""HRM Phase 13: access tokens are bound to their refresh session, so logout and rotation end them."""

from uuid import uuid4

from sqlalchemy import select

from src.core.security import create_access_token, decode_token, hash_password
from src.db.models import RefreshToken, School, SchoolMembership, User
from src.modules.auth.service import AuthService

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
EMPLOYEES = "/api/v1/hr/employees"
PASSWORD = "Lifecycle-Test-123!"


async def _login(api, db, actor, portal="hrm"):
    user = await db.get(User, actor.user_id)
    user.email = f"lifecycle-{actor.user_id.hex[:12]}@example.com"
    user.password_hash = hash_password(PASSWORD)
    await db.flush()
    r = await api.post(LOGIN, json={"email": user.email, "password": PASSWORD, "portal": portal})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_login_binds_access_token_to_refresh_session(api, db_session, hr_world):
    tokens = await _login(api, db_session, hr_world.admin_a)
    access, refresh = decode_token(tokens["access_token"]), decode_token(tokens["refresh_token"])
    assert access["sid"] == refresh["jti"]
    assert (await api.get(EMPLOYEES, headers=_bearer(tokens["access_token"]))).status_code == 200


async def test_logout_ends_the_access_token_immediately(api, db_session, hr_world):
    tokens = await _login(api, db_session, hr_world.admin_a)
    assert (await api.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]})).status_code == 200

    for path in (ME, EMPLOYEES):
        r = await api.get(path, headers=_bearer(tokens["access_token"]))
        assert r.status_code == 401, (path, r.text)
        assert r.json()["error"]["message"] == "Session has ended"
    r = await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


async def test_refresh_rotates_and_retires_the_previous_access_token(api, db_session, hr_world):
    first = await _login(api, db_session, hr_world.teacher_a, portal="teacher")
    r = await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200, r.text
    second = r.json()

    assert (await api.get(ME, headers=_bearer(first["access_token"]))).status_code == 401
    assert (await api.get(ME, headers=_bearer(second["access_token"]))).status_code == 200
    # A replayed refresh token is rejected, and does not disturb the live session.
    assert (await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})).status_code == 401
    assert (await api.get(ME, headers=_bearer(second["access_token"]))).status_code == 200


async def test_disabled_account_cannot_refresh_or_use_its_token(api, db_session, hr_world):
    tokens = await _login(api, db_session, hr_world.teacher_a, portal="teacher")
    user = await db_session.get(User, hr_world.teacher_a.user_id)
    user.is_active = False
    await db_session.flush()

    assert (await api.get(ME, headers=_bearer(tokens["access_token"]))).status_code == 401
    r = await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Account disabled"


async def _membership(db, actor):
    return (
        await db.execute(select(SchoolMembership).where(SchoolMembership.user_id == actor.user_id))
    ).scalar_one()


async def test_suspended_membership_loses_access_and_refresh_at_once(api, db_session, hr_world):
    tokens = await _login(api, db_session, hr_world.admin_a)
    membership = await _membership(db_session, hr_world.admin_a)
    membership.status = "SUSPENDED"
    await db_session.flush()

    r = await api.get(EMPLOYEES, headers=_bearer(tokens["access_token"]))
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "School membership is not active"
    r = await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "School membership is not active"


async def test_role_change_forces_a_new_token_with_the_current_role(api, db_session, hr_world):
    tokens = await _login(api, db_session, hr_world.admin_a)
    membership = await _membership(db_session, hr_world.admin_a)
    membership.role = "TEACHER"
    await db_session.flush()

    r = await api.get(EMPLOYEES, headers=_bearer(tokens["access_token"]))
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Session has ended"
    renewed = await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["role"] == "TEACHER"
    r = await api.get(EMPLOYEES, headers=_bearer(renewed.json()["access_token"]))
    assert r.status_code == 403


async def test_refresh_keeps_the_signed_in_membership(api, db_session, hr_world):
    teacher = hr_world.teacher_a
    other = School(id=uuid4(), name=f"IT Other {uuid4().hex[:8]}", slug=f"it-other-{uuid4().hex[:10]}")
    db_session.add(other)
    await db_session.flush()
    db_session.add(SchoolMembership(user_id=teacher.user_id, school_id=other.id, role="SCHOOL_ADMIN"))
    await db_session.flush()

    tokens = await _login(api, db_session, teacher, portal="teacher")
    assert tokens["role"] == "TEACHER"
    for _ in range(2):
        r = await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 200, r.text
        tokens = r.json()
        assert tokens["role"] == "TEACHER"
        assert tokens["school_id"] == str(teacher.school_id)


async def test_refresh_tokens_issued_before_membership_binding_still_work(api, db_session, hr_world):
    raw = await AuthService(db_session)._issue_refresh(hr_world.admin_b.user_id)
    r = await api.post(REFRESH, json={"refresh_token": raw})
    assert r.status_code == 200, r.text
    assert r.json()["school_id"] == str(hr_world.admin_b.school_id)
    assert decode_token(r.json()["refresh_token"])["membership_id"]


async def test_unknown_or_foreign_session_id_is_rejected(api, db_session, hr_world):
    teacher_tokens = await _login(api, db_session, hr_world.teacher_a, portal="teacher")
    teacher_sid = decode_token(teacher_tokens["access_token"])["sid"]
    admin = hr_world.admin_a
    for sid in ("not-a-session", teacher_sid):
        forged = create_access_token(
            subject=admin.user_id, role="SCHOOL_ADMIN", school_id=admin.school_id, extra={"sid": sid}
        )
        assert (await api.get(EMPLOYEES, headers=_bearer(forged))).status_code == 401


async def test_tokens_without_session_id_keep_working(api, hr_world):
    # Tokens issued before session binding carry no sid and stay valid until they expire.
    assert (await api.get(EMPLOYEES, headers=hr_world.admin_a.headers)).status_code == 200


async def test_auth_errors_do_not_echo_tokens(api, db_session, hr_world):
    tokens = await _login(api, db_session, hr_world.admin_a)
    await api.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]})
    responses = [
        await api.get(ME, headers=_bearer(tokens["access_token"])),
        await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}),
        await api.post(REFRESH, json={"refresh_token": "garbage.token.value"}),
    ]
    for r in responses:
        assert r.status_code == 401
        assert tokens["access_token"] not in r.text
        assert tokens["refresh_token"] not in r.text
        assert "garbage.token.value" not in r.text
        assert r.headers.get("x-request-id")
    rows = (
        await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == hr_world.admin_a.user_id))
    ).scalars().all()
    assert rows and all(row.revoked for row in rows)
