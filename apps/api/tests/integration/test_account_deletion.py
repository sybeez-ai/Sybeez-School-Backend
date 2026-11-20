"""POST /auth/delete-account: re-auth, anonymize in place, revoke everything, audit, RLS."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.core.security import create_access_token, hash_password
from src.db.models import School, SchoolMembership, User
from src.modules.auth import cognito as cognito_svc

URL = "/api/v1/auth/delete-account"
PW = "Delete-Me-Test-123!"


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _scalar(db, sql, **params):
    return (await db.execute(text(sql), params)).scalar_one()


async def _prep(db, actor, email_tag="del"):
    user = await db.get(User, actor.user_id)
    user.email = f"{email_tag}-{actor.user_id.hex[:10]}@example.com"
    user.password_hash = hash_password(PW)
    user.phone = "+353871234567"
    user.avatar_url = "https://example.com/a.png"
    await db.flush()
    return user


async def _seed_private(db, actor):
    await db.execute(
        text("INSERT INTO refresh_tokens (id, user_id, jti, token_hash, expires_at, family_expires_at, revoked, absolute_session, created_at, updated_at) "
             "VALUES (gen_random_uuid(), :u, :j, 'x', now() + interval '1 day', now() + interval '1 day', false, false, now(), now())"),
        {"u": actor.user_id, "j": uuid4().hex},
    )
    await db.execute(
        text("INSERT INTO push_devices (id, user_id, school_id, token, platform, provider, last_seen_at, created_at, updated_at) "
             "VALUES (gen_random_uuid(), :u, :s, :t, 'ios', 'expo', now(), now(), now())"),
        {"u": actor.user_id, "s": actor.school_id, "t": f"ExponentPushToken[{uuid4().hex}]"},
    )
    await db.execute(
        text("INSERT INTO personal_notes (id, school_id, user_id, subject, chapter, title, body, tags, color, pinned, is_deleted, created_at, updated_at) "
             "VALUES (gen_random_uuid(), :s, :u, '', '', 'secret', 'private body', '[]'::jsonb, 'blue', false, false, now(), now())"),
        {"s": actor.school_id, "u": actor.user_id},
    )
    await db.flush()


async def test_student_deletes_account_end_to_end(api, db_session, hr_world):
    actor = hr_world.student_a
    user = await _prep(db_session, actor)
    old_email = user.email
    await _seed_private(db_session, actor)

    r = await api.post(URL, json={"confirm": "DELETE", "password": PW}, headers=_h(actor.token))
    assert r.status_code == 200, r.text

    row = (await db_session.execute(text("SELECT * FROM users WHERE id = :u"), {"u": actor.user_id})).mappings().one()
    assert row["email"] == f"deleted-{actor.user_id}@deleted.invalid"
    assert row["full_name"] == "Deleted user"
    assert row["is_active"] is False and row["status"] == "DELETED"
    assert row["password_hash"] is None and row["cognito_sub"] is None
    assert row["phone"] is None and row["avatar_url"] is None
    for table in ("refresh_tokens", "push_devices", "personal_notes"):
        assert await _scalar(db_session, f"SELECT count(*) FROM {table} WHERE user_id = :u", u=actor.user_id) == 0
    assert await _scalar(
        db_session, "SELECT count(*) FROM school_memberships WHERE user_id = :u AND status <> 'LEFT'", u=actor.user_id
    ) == 0

    # Old token and old credentials are dead.
    assert (await api.get("/api/v1/auth/me", headers=_h(actor.token))).status_code == 401
    login = await api.post("/api/v1/auth/login", json={"email": old_email, "password": PW})
    assert login.status_code == 401

    # Audit: one platform-side record without PII, one per school.
    assert await _scalar(db_session, "SELECT count(*) FROM account_deletions WHERE user_id = :u", u=actor.user_id) == 1
    dump = str(
        (await db_session.execute(text("SELECT * FROM account_deletions WHERE user_id = :u"), {"u": actor.user_id})).mappings().all()
    )
    assert old_email not in dump
    assert await _scalar(
        db_session,
        "SELECT count(*) FROM audit_logs WHERE action = 'account.deleted' AND resource_id = :r AND school_id = :s",
        r=str(actor.user_id),
        s=actor.school_id,
    ) == 1


async def test_wrong_password_changes_nothing_and_is_rate_limited(api, db_session, hr_world, monkeypatch):
    actor = hr_world.student_a
    await _prep(db_session, actor)
    await _seed_private(db_session, actor)
    store: dict = {}

    class FakeRedis:
        async def get(self, k):
            return store.get(k)

        async def incr(self, k):
            store[k] = int(store.get(k, 0)) + 1
            return store[k]

        async def expire(self, *_):
            return True

        async def delete(self, k):
            store.pop(k, None)

    async def fake():
        return FakeRedis()

    monkeypatch.setattr("src.modules.auth.account_deletion.get_redis", fake)
    for _ in range(5):
        r = await api.post(URL, json={"confirm": "DELETE", "password": "nope-nope-nope"}, headers=_h(actor.token))
        assert r.status_code == 401
    assert (await api.post(URL, json={"confirm": "DELETE", "password": PW}, headers=_h(actor.token))).status_code == 429
    user = await db_session.get(User, actor.user_id)
    await db_session.refresh(user)
    assert user.is_active and user.password_hash
    assert await _scalar(db_session, "SELECT count(*) FROM push_devices WHERE user_id = :u", u=actor.user_id) == 1


async def test_confirmation_phrase_and_auth_required(api, db_session, hr_world):
    actor = hr_world.student_a
    await _prep(db_session, actor)
    assert (await api.post(URL, json={"password": PW}, headers=_h(actor.token))).status_code == 422
    assert (await api.post(URL, json={"confirm": "delete", "password": PW}, headers=_h(actor.token))).status_code == 422
    assert (await api.post(URL, json={"confirm": "DELETE", "password": PW})).status_code in (401, 403)
    assert (await api.post(URL, json={"confirm": "DELETE"}, headers=_h(actor.token))).status_code == 401


async def test_school_admin_and_platform_accounts_are_refused(api, db_session, hr_world):
    actor = hr_world.admin_a
    await _prep(db_session, actor)
    r = await api.post(URL, json={"confirm": "DELETE", "password": PW}, headers=_h(actor.token))
    assert r.status_code == 409 and "account_deletion_not_allowed" in r.text
    user = await db_session.get(User, actor.user_id)
    await db_session.refresh(user)
    assert user.is_active


async def test_teacher_in_two_schools_is_cleared_everywhere_and_other_users_untouched(api, db_session, hr_world):
    actor = hr_world.teacher_a
    other = hr_world.teacher_a2
    await _prep(db_session, actor)
    await _prep(db_session, other, "other")
    school_b = hr_world.admin_b.school_id
    db_session.add(SchoolMembership(user_id=actor.user_id, school_id=school_b, role="TEACHER"))
    await db_session.execute(
        text("UPDATE teachers SET phone = '123', qualification = 'PhD' WHERE user_id = :u"), {"u": actor.user_id}
    )
    await db_session.flush()

    r = await api.post(URL, json={"confirm": "DELETE", "password": PW}, headers=_h(actor.token))
    assert r.status_code == 200, r.text
    t = (await db_session.execute(text("SELECT phone, qualification, status FROM teachers WHERE user_id = :u"), {"u": actor.user_id})).one()
    assert t.phone is None and t.qualification is None and t.status == "LEFT"
    assert await _scalar(
        db_session, "SELECT count(*) FROM audit_logs WHERE action = 'account.deleted' AND resource_id = :r", r=str(actor.user_id)
    ) == 2  # one per school, including school B the token was not scoped to
    assert await _scalar(db_session, "SELECT is_active FROM users WHERE id = :u", u=other.user_id) is True
    assert (await api.get("/api/v1/auth/me", headers=_h(other.token))).status_code == 200


async def test_parent_deletion_keeps_the_child_record(api, db_session, hr_world):
    school = hr_world.admin_a.school_id
    user = User(id=uuid4(), email=f"parent-{uuid4().hex[:8]}@example.com", full_name="Pat Parent", password_hash=hash_password(PW), phone="1")
    db_session.add(user)
    await db_session.flush()
    db_session.add(SchoolMembership(user_id=user.id, school_id=school, role="PARENT"))
    await db_session.execute(
        text("INSERT INTO parents (id, school_id, user_id, phone, status, created_at, updated_at) VALUES (gen_random_uuid(), :s, :u, '1', 'ACTIVE', now(), now())"),
        {"s": school, "u": user.id},
    )
    await db_session.flush()
    token = create_access_token(subject=user.id, role="PARENT", school_id=school)
    before = await _scalar(db_session, "SELECT count(*) FROM students WHERE school_id = :s", s=school)
    r = await api.post(URL, json={"confirm": "DELETE", "password": PW}, headers=_h(token))
    assert r.status_code == 200, r.text
    assert await _scalar(db_session, "SELECT phone IS NULL FROM parents WHERE user_id = :u", u=user.id) is True
    assert await _scalar(db_session, "SELECT count(*) FROM students WHERE school_id = :s", s=school) == before


async def test_cognito_account_reauths_with_cognito_and_removes_pool_user(api, db_session, hr_world, monkeypatch):
    actor = hr_world.student_a
    user = await _prep(db_session, actor, "cog")
    user.cognito_sub = f"sub-{uuid4().hex}"
    user.password_hash = None
    await db_session.flush()
    calls: list[str] = []
    monkeypatch.setattr(cognito_svc, "cognito_configured", lambda: True)
    monkeypatch.setattr(
        cognito_svc, "authenticate",
        lambda email, password: (_ for _ in ()).throw(__import__("src.core.exceptions", fromlist=["x"]).UnauthorizedError("bad"))
        if password != PW else cognito_svc.CognitoAuthResult("a", "i", "r", "s", email),
    )
    monkeypatch.setattr(cognito_svc, "disable_user_for_deletion", lambda *, email: calls.append("disable") or "disabled")
    monkeypatch.setattr(cognito_svc, "delete_user_after_deletion", lambda *, email: calls.append("delete") or True)

    bad = await api.post(URL, json={"confirm": "DELETE", "password": "wrong-wrong-1"}, headers=_h(actor.token))
    assert bad.status_code == 401 and calls == []
    ok = await api.post(URL, json={"confirm": "DELETE", "password": PW}, headers=_h(actor.token))
    assert ok.status_code == 200, ok.text
    assert calls == ["disable", "delete"]
    assert await _scalar(db_session, "SELECT cognito_status FROM account_deletions WHERE user_id = :u", u=actor.user_id) == "disabled"


async def test_cognito_disable_failure_aborts_before_any_change(api, db_session, hr_world, monkeypatch):
    actor = hr_world.student_a
    await _prep(db_session, actor)
    monkeypatch.setattr(cognito_svc, "disable_user_for_deletion", lambda *, email: (_ for _ in ()).throw(RuntimeError("down")))
    r = await api.post(URL, json={"confirm": "DELETE", "password": PW}, headers=_h(actor.token))
    assert r.status_code >= 500
    assert await _scalar(db_session, "SELECT is_active FROM users WHERE id = :u", u=actor.user_id) is True


async def test_social_account_needs_email_and_fresh_session(api, db_session, hr_world):
    actor = hr_world.student_a
    user = await _prep(db_session, actor, "soc")
    user.password_hash = None
    await db_session.flush()
    fresh = create_access_token(subject=actor.user_id, role="STUDENT", school_id=actor.school_id)
    assert (await api.post(URL, json={"confirm": "DELETE"}, headers=_h(fresh))).status_code == 401
    assert (await api.post(URL, json={"confirm": "DELETE", "confirm_email": "x@example.com"}, headers=_h(fresh))).status_code == 401
    ok = await api.post(URL, json={"confirm": "DELETE", "confirm_email": user.email}, headers=_h(fresh))
    assert ok.status_code == 200, ok.text


async def test_function_and_table_rls_as_production_role(db_session, hr_world):
    """sybeez_app: cannot read the audit table, cannot delete someone else, can delete itself."""
    actor, other = hr_world.student_a, hr_world.teacher_a
    await db_session.execute(text("SET ROLE sybeez_app"))
    for k, v in (("app.user_id", actor.user_id), ("app.school_id", actor.school_id), ("app.role", "STUDENT"), ("app.rls_bypass", "off")):
        await db_session.execute(text("SELECT set_config(:k, :v, false)"), {"k": k, "v": str(v)})
    await db_session.execute(text("SELECT app.delete_my_account('password', 'skipped', NULL)"))
    # The caller can no longer read the audit table (platform/service only) ...
    assert await _scalar(db_session, "SELECT count(*) FROM account_deletions") == 0
    # ... and cannot write it directly.
    with pytest.raises(Exception):
        async with db_session.begin_nested():
            await db_session.execute(
                text("INSERT INTO account_deletions (user_id, reauth_method) VALUES (gen_random_uuid(), 'x')")
            )
    await db_session.execute(text("RESET ROLE"))
    assert await _scalar(db_session, "SELECT is_active FROM users WHERE id = :u", u=actor.user_id) is False
    assert await _scalar(db_session, "SELECT is_active FROM users WHERE id = :u", u=other.user_id) is True
    assert await _scalar(db_session, "SELECT count(*) FROM account_deletions WHERE user_id = :u", u=actor.user_id) == 1


async def test_function_refuses_anonymous_caller(db_session, hr_world):
    await db_session.execute(text("SET ROLE sybeez_app"))
    await db_session.execute(text("SELECT set_config('app.user_id', '', false)"))
    with pytest.raises(Exception, match="authenticated user"):
        async with db_session.begin_nested():
            await db_session.execute(text("SELECT app.delete_my_account('password', 'skipped', NULL)"))
    await db_session.execute(text("RESET ROLE"))
