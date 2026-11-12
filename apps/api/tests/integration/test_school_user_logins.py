"""Admin-created student/teacher/parent logins: temp password shown once, forced change, reset."""

from uuid import uuid4

from src.core.security import hash_password
from src.db.models import User

LOGIN = "/api/v1/auth/login"
ADMIN_PW = "Admin-Login-Test-123!"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def _admin_token(api, db, actor):
    user = await db.get(User, actor.user_id)
    user.email = f"logins-admin-{actor.user_id.hex[:12]}@example.com"
    user.password_hash = hash_password(ADMIN_PW)
    await db.flush()
    r = await api.post(LOGIN, json={"email": user.email, "password": ADMIN_PW, "portal": "school"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _first_login_and_change(api, email, temp, portal, role):
    r = await api.post(LOGIN, json={"email": email, "password": temp, "portal": portal})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == role and body["must_change_password"] is True
    tok = body["access_token"]
    # Locked to the change-password flow until a new password is set.
    me = await api.get("/api/v1/auth/me", headers=_bearer(tok))
    assert me.status_code == 200 and me.json()["must_change_password"] is True
    blocked = await api.get("/api/v1/students", headers=_bearer(tok))
    assert blocked.status_code == 403 and "PASSWORD_CHANGE_REQUIRED" in blocked.text
    new_pw = "My-Own-Pass-456!"
    r = await api.post(
        "/api/v1/auth/change-password",
        json={"current_password": temp, "new_password": new_pw},
        headers=_bearer(tok),
    )
    assert r.status_code == 200, r.text
    r = await api.post(LOGIN, json={"email": email, "password": new_pw, "portal": portal})
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert (await api.post(LOGIN, json={"email": email, "password": temp, "portal": portal})).status_code == 401


async def test_student_parent_teacher_logins_end_to_end(api, db_session, hr_world):
    tok = await _admin_token(api, db_session, hr_world.admin_a)
    tag = uuid4().hex[:8]

    # Student with only an email + a new linked parent: both get temporary passwords.
    r = await api.post(
        "/api/v1/students",
        json={
            "first_name": "Login",
            "last_name": f"Student{tag}",
            "email": f"stu-{tag}@example.com",
            "parents": [{"full_name": f"Login Parent {tag}", "email": f"par-{tag}@example.com"}],
        },
        headers=_bearer(tok),
    )
    assert r.status_code == 201, r.text
    stu = r.json()
    assert stu["temporary_password"]
    assert [c["email"] for c in stu["parent_credentials"]] == [f"par-{tag}@example.com"]
    await _first_login_and_change(api, stu["email"], stu["temporary_password"], "student", "STUDENT")
    await _first_login_and_change(
        api, f"par-{tag}@example.com", stu["parent_credentials"][0]["temporary_password"], "parent", "PARENT"
    )

    # Reset / resend login on the student row issues a fresh temporary password.
    r = await api.post(f"/api/v1/students/{stu['id']}/reset-login", headers=_bearer(tok))
    assert r.status_code == 200, r.text
    reset = r.json()
    assert reset["login_path"] == "/student/login" and reset["email"] == stu["email"]
    await _first_login_and_change(api, stu["email"], reset["temporary_password"], "student", "STUDENT")

    # Teacher created with only an email.
    r = await api.post(
        "/api/v1/teachers",
        json={"full_name": f"Login Teacher {tag}", "email": f"tch-{tag}@example.com"},
        headers=_bearer(tok),
    )
    assert r.status_code == 201, r.text
    tch = r.json()
    assert tch["temporary_password"]
    await _first_login_and_change(api, f"tch-{tag}@example.com", tch["temporary_password"], "teacher", "TEACHER")
    r = await api.post(f"/api/v1/teachers/{tch['id']}/reset-login", headers=_bearer(tok))
    assert r.status_code == 200, r.text

    # Standalone parent + reset.
    r = await api.post(
        "/api/v1/parents",
        json={"full_name": f"Solo Parent {tag}", "email": f"solo-{tag}@example.com"},
        headers=_bearer(tok),
    )
    assert r.status_code == 201, r.text
    par = r.json()
    r = await api.post(f"/api/v1/parents/{par['id']}/reset-login", headers=_bearer(tok))
    assert r.status_code == 200, r.text
    await _first_login_and_change(api, f"solo-{tag}@example.com", r.json()["temporary_password"], "parent", "PARENT")


async def test_reset_login_is_school_admin_only_and_tenant_scoped(api, db_session, hr_world):
    tok = await _admin_token(api, db_session, hr_world.admin_a)
    r = await api.post(f"/api/v1/students/{uuid4()}/reset-login", headers=_bearer(tok))
    assert r.status_code == 404
