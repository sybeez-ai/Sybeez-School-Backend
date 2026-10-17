"""Every role's login lifecycle: created by an admin, temp password once, forced change, admin reset."""

from uuid import uuid4

from src.core.security import create_access_token, hash_password
from src.db.models import User

LOGIN = "/api/v1/auth/login"
PW = "Admin-Login-Test-123!"
NEW = "My-Own-Pass-456!"


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _admin_token(api, db, actor):
    user = await db.get(User, actor.user_id)
    user.email = f"lc-admin-{actor.user_id.hex[:12]}@example.com"
    user.password_hash = hash_password(PW)
    await db.flush()
    r = await api.post(LOGIN, json={"email": user.email, "password": PW, "portal": "school"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _first_login_change(api, email, temp, portal, role):
    r = await api.post(LOGIN, json={"email": email, "password": temp, "portal": portal})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == role and body["must_change_password"] is True
    tok = body["access_token"]
    blocked = await api.get("/api/v1/timetable/today", headers=_h(tok))
    assert blocked.status_code == 403 and "PASSWORD_CHANGE_REQUIRED" in blocked.text
    r = await api.post("/api/v1/auth/change-password", json={"current_password": temp, "new_password": NEW}, headers=_h(tok))
    assert r.status_code == 200, r.text
    ok = await api.post(LOGIN, json={"email": email, "password": NEW, "portal": portal})
    assert ok.status_code == 200 and ok.json()["must_change_password"] is False
    assert (await api.post(LOGIN, json={"email": email, "password": temp, "portal": portal})).status_code == 401
    return ok.json()["access_token"]


async def test_staff_employee_login_forced_change_and_admin_reset(api, db_session, hr_world):
    tok = await _admin_token(api, db_session, hr_world.admin_a)
    tag = uuid4().hex[:8]
    email = f"staff-{tag}@example.com"
    r = await api.post(
        "/api/v1/hr/employees",
        json={"staff_type": "NON_TEACHING", "full_name": f"Staff {tag}", "email": email, "grant_access": True},
        headers=_h(tok),
    )
    assert r.status_code == 201, r.text
    emp = r.json()
    assert emp["temporary_password"]
    await _first_login_change(api, email, emp["temporary_password"], "staff", "STAFF")

    r = await api.post(f"/api/v1/hr/employees/{emp['id']}/reset-login", headers=_h(tok))
    assert r.status_code == 200, r.text
    reset = r.json()
    assert reset["email"] == email and reset["temporary_password"] and reset["must_change_password"] is True
    # The old (changed) password no longer works; the new temporary one forces another change.
    assert (await api.post(LOGIN, json={"email": email, "password": NEW, "portal": "staff"})).status_code == 401
    await _first_login_change(api, email, reset["temporary_password"], "staff", "STAFF")


async def test_hrm_role_grant_login_and_membership_reset(api, db_session, hr_world):
    tok = await _admin_token(api, db_session, hr_world.admin_a)
    tag = uuid4().hex[:8]
    email = f"hrm-{tag}@example.com"
    r = await api.post(
        "/api/v1/access/role-grants",
        json={"email": email, "full_name": f"Hr Person {tag}", "password": "Temp-Hr-Pass-123!", "role": "HR_MANAGER"},
        headers=_h(tok),
    )
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    await _first_login_change(api, email, "Temp-Hr-Pass-123!", "hrm", "HR_MANAGER")

    r = await api.post(f"/api/v1/access/memberships/{mid}/reset-login", headers=_h(tok))
    assert r.status_code == 200, r.text
    await _first_login_change(api, email, r.json()["temporary_password"], "hrm", "HR_MANAGER")


async def test_reset_member_login_refuses_admins_and_other_schools(api, db_session, hr_world):
    tok = await _admin_token(api, db_session, hr_world.admin_a)
    from sqlalchemy import text

    other_admin_membership = (
        await db_session.execute(
            text("SELECT id FROM school_memberships WHERE user_id = :u"), {"u": hr_world.admin_b.user_id}
        )
    ).scalar_one()
    r = await api.post(f"/api/v1/access/memberships/{other_admin_membership}/reset-login", headers=_h(tok))
    assert r.status_code == 404  # another school's membership is invisible
    own_admin = (
        await db_session.execute(
            text("SELECT id FROM school_memberships WHERE user_id = :u"), {"u": hr_world.admin_a.user_id}
        )
    ).scalar_one()
    r = await api.post(f"/api/v1/access/memberships/{own_admin}/reset-login", headers=_h(tok))
    assert r.status_code in (403, 409)
    # A teacher cannot reset anyone.
    r = await api.post(f"/api/v1/access/memberships/{own_admin}/reset-login", headers=_h(hr_world.teacher_a.token))
    assert r.status_code == 403


async def test_platform_reset_school_admin_generates_forces_change_and_revokes(api, db_session, hr_world):
    plat = await db_session.get(User, hr_world.admin_b.user_id)  # reuse a user as the platform actor
    plat.is_platform_admin = True
    plat.platform_role = "SUPER_ADMIN"
    await db_session.flush()
    ptok = create_access_token(subject=plat.id, role="PLATFORM_ADMIN", school_id=None, extra={"platform_role": "SUPER_ADMIN"})
    school_id = hr_world.admin_a.school_id
    admin = await db_session.get(User, hr_world.admin_a.user_id)
    admin.email = f"sa-{uuid4().hex[:8]}@example.com"
    admin.password_hash = hash_password(PW)
    await db_session.flush()
    first = await api.post(LOGIN, json={"email": admin.email, "password": PW, "portal": "school"})
    assert first.status_code == 200

    r = await api.post(f"/api/v1/platform/schools/{school_id}/admin/reset-password", json={}, headers=_h(ptok))
    assert r.status_code == 200, r.text
    temp = r.json()["temporary_password"]
    assert len(temp) >= 10
    # Previous session is revoked, old password dead, new one forces a change.
    assert (await api.get("/api/v1/auth/me", headers=_h(first.json()["access_token"]))).status_code == 401
    assert (await api.post(LOGIN, json={"email": admin.email, "password": PW, "portal": "school"})).status_code == 401
    await _first_login_change(api, admin.email, temp, "school", "SCHOOL_ADMIN")
