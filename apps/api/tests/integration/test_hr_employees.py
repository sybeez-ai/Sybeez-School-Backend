"""HRM Phase 2: employee directory for teaching and non-teaching staff."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.db.models import Employee, Teacher, User

EMP = "/api/v1/hr/employees"


async def _staff(api, admin, **over):
    body = {
        "staff_type": "NON_TEACHING",
        "full_name": "Ravi Kumar",
        "phone": "9000000001",
        "employee_code": f"NT-{uuid4().hex[:6]}",
        "employment_type": "FULL_TIME",
        "emergency_contact_name": "Asha Kumar",
        "emergency_contact_phone": "9000000002",
    }
    body.update(over)
    return await api.post(EMP, json=body, headers=admin.headers)


async def _list(api, admin, **params):
    r = await api.get(EMP, params=params, headers=admin.headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── Directory ───────────────────────────────────────────────


async def test_existing_teachers_appear_as_teaching_staff(api, hr_world):
    rows = await _list(api, hr_world.admin_a)

    teacher_ids = {r["teacher_id"] for r in rows if r["staff_type"] == "TEACHING"}
    assert teacher_ids == {str(hr_world.teacher_a.teacher_id), str(hr_world.teacher_a2.teacher_id)}
    assert all(r["has_login"] for r in rows if r["staff_type"] == "TEACHING")


async def test_listing_twice_does_not_duplicate_teacher_rows(api, hr_world, db_session):
    await _list(api, hr_world.admin_a)
    await _list(api, hr_world.admin_a)

    count = len(
        (
            await db_session.execute(select(Employee).where(Employee.school_id == hr_world.admin_a.school_id))
        ).scalars().all()
    )
    assert count == 2


async def test_create_non_teaching_staff_persists_without_login(api, hr_world, db_session):
    r = await _staff(api, hr_world.admin_a, full_name="Meena Driver", employment_type="CONTRACT")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["staff_type"] == "NON_TEACHING"
    assert body["has_login"] is False
    assert body["user_id"] is None
    assert body["is_active"] is True
    assert body["employment_type"] == "CONTRACT"
    row = await db_session.get(Employee, body["id"])
    assert row.full_name == "Meena Driver"
    assert row.teacher_id is None


async def test_create_teaching_staff_reuses_teacher_creation(api, hr_world, db_session):
    email = f"it-{uuid4().hex[:8]}@example.com"
    r = await _staff(
        api,
        hr_world.admin_a,
        staff_type="TEACHING",
        full_name="Neha Sharma",
        email=email,
        password="StartPass123!",
        title="PGT Maths",
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["staff_type"] == "TEACHING"
    assert body["has_login"] is True
    assert body["temporary_password"] == "StartPass123!"
    teacher = await db_session.get(Teacher, body["teacher_id"])
    assert teacher.school_id == hr_world.admin_a.school_id
    assert teacher.title == "PGT Maths"
    user = await db_session.get(User, teacher.user_id)
    assert user.email == email
    assert body["employment_type"] == "FULL_TIME"


async def test_teaching_staff_requires_email(api, hr_world):
    r = await _staff(api, hr_world.admin_a, staff_type="TEACHING")
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "override",
    [{"full_name": ""}, {"employment_type": "INTERN"}, {"staff_type": "VOLUNTEER"}, {"email": "not-an-email"}],
)
async def test_create_validation(api, hr_world, override):
    r = await _staff(api, hr_world.admin_a, **override)
    assert r.status_code == 422, r.text


async def test_employee_code_unique_across_staff_kinds(api, hr_world, db_session):
    teacher = await db_session.get(Teacher, hr_world.teacher_a.teacher_id)
    teacher.employee_code = "EMP-777"
    await db_session.flush()

    clash_teacher = await _staff(api, hr_world.admin_a, employee_code="emp-777")
    first = await _staff(api, hr_world.admin_a, employee_code="NT-900")
    clash_staff = await _staff(api, hr_world.admin_a, employee_code="nt-900")
    teacher_patch = await api.patch(
        f"/api/v1/teachers/{hr_world.teacher_a.teacher_id}",
        json={"employee_code": "NT-900"},
        headers=hr_world.admin_a.headers,
    )

    assert clash_teacher.status_code == 409, clash_teacher.text
    assert first.status_code == 201, first.text
    assert clash_staff.status_code == 409, clash_staff.text
    assert teacher_patch.status_code == 409, teacher_patch.text


async def test_same_code_allowed_in_another_school(api, hr_world):
    a = await _staff(api, hr_world.admin_a, employee_code="SHARED-1")
    b = await _staff(api, hr_world.admin_b, employee_code="SHARED-1")
    assert (a.status_code, b.status_code) == (201, 201)


async def test_filters(api, hr_world):
    await _staff(api, hr_world.admin_a, full_name="Gopal Guard", employment_type="CONTRACT")
    off = (await _staff(api, hr_world.admin_a, full_name="Sita Cleaner", employment_type="PART_TIME")).json()
    await api.post(f"{EMP}/{off['id']}/deactivate", headers=hr_world.admin_a.headers)

    non_teaching = await _list(api, hr_world.admin_a, staff_type="NON_TEACHING")
    teaching = await _list(api, hr_world.admin_a, staff_type="TEACHING")
    contract = await _list(api, hr_world.admin_a, employment_type="CONTRACT")
    search = await _list(api, hr_world.admin_a, q="gopal")
    inactive = await _list(api, hr_world.admin_a, status="INACTIVE")
    active = await _list(api, hr_world.admin_a, status="ACTIVE")

    assert {r["full_name"] for r in non_teaching} == {"Gopal Guard", "Sita Cleaner"}
    assert len(teaching) == 2 and all(r["staff_type"] == "TEACHING" for r in teaching)
    assert [r["full_name"] for r in contract] == ["Gopal Guard"]
    assert [r["full_name"] for r in search] == ["Gopal Guard"]
    assert [r["full_name"] for r in inactive] == ["Sita Cleaner"]
    assert "Sita Cleaner" not in {r["full_name"] for r in active}
    assert len(active) == 3


async def test_invalid_filter_value_is_422(api, hr_world):
    r = await api.get(EMP, params={"staff_type": "ALIEN"}, headers=hr_world.admin_a.headers)
    assert r.status_code == 422


# ── Details / edit / status ─────────────────────────────────


async def test_update_non_teaching_staff(api, hr_world, db_session):
    emp = (await _staff(api, hr_world.admin_a)).json()

    r = await api.patch(
        f"{EMP}/{emp['id']}",
        json={"full_name": "Ravi K. Kumar", "address": "12 MG Road", "notes": "Night shift", "status": "INACTIVE"},
        headers=hr_world.admin_a.headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Ravi K. Kumar"
    assert r.json()["status"] == "INACTIVE"
    left = await api.patch(f"{EMP}/{emp['id']}", json={"status": "LEFT"}, headers=hr_world.admin_a.headers)
    assert left.status_code == 422 and "exit action" in left.json()["error"]["message"]
    got = (await api.get(f"{EMP}/{emp['id']}", headers=hr_world.admin_a.headers)).json()
    assert got["address"] == "12 MG Road"
    assert got["notes"] == "Night shift"


async def test_update_teaching_staff_writes_through_to_teacher(api, hr_world, db_session):
    rows = await _list(api, hr_world.admin_a, staff_type="TEACHING")
    emp = next(r for r in rows if r["teacher_id"] == str(hr_world.teacher_a.teacher_id))

    r = await api.patch(
        f"{EMP}/{emp['id']}",
        json={"full_name": "Renamed Teacher", "qualification": "M.Sc", "employment_type": "PART_TIME"},
        headers=hr_world.admin_a.headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Renamed Teacher"
    assert r.json()["employment_type"] == "PART_TIME"
    teacher = await db_session.get(Teacher, hr_world.teacher_a.teacher_id, populate_existing=True)
    user = await db_session.get(User, hr_world.teacher_a.user_id, populate_existing=True)
    assert teacher.qualification == "M.Sc"
    assert user.full_name == "Renamed Teacher"


async def test_teacher_login_email_cannot_change_from_hrm(api, hr_world):
    rows = await _list(api, hr_world.admin_a, staff_type="TEACHING")

    r = await api.patch(
        f"{EMP}/{rows[0]['id']}", json={"email": "new-login@example.com"}, headers=hr_world.admin_a.headers
    )

    assert r.status_code == 422, r.text


async def test_deactivate_and_activate_teaching_staff_toggles_login(api, hr_world, db_session):
    rows = await _list(api, hr_world.admin_a, staff_type="TEACHING")
    emp = next(r for r in rows if r["teacher_id"] == str(hr_world.teacher_a.teacher_id))

    off = await api.post(f"{EMP}/{emp['id']}/deactivate", headers=hr_world.admin_a.headers)
    user = await db_session.get(User, hr_world.teacher_a.user_id, populate_existing=True)
    assert off.status_code == 200, off.text
    assert off.json()["is_active"] is False
    assert user.is_active is False

    on = await api.post(f"{EMP}/{emp['id']}/activate", headers=hr_world.admin_a.headers)
    user = await db_session.get(User, hr_world.teacher_a.user_id, populate_existing=True)
    assert on.json()["is_active"] is True
    assert user.is_active is True


async def test_deactivate_and_activate_non_teaching_staff(api, hr_world):
    emp = (await _staff(api, hr_world.admin_a)).json()

    off = await api.post(f"{EMP}/{emp['id']}/deactivate", headers=hr_world.admin_a.headers)
    on = await api.post(f"{EMP}/{emp['id']}/activate", headers=hr_world.admin_a.headers)

    assert (off.json()["status"], off.json()["is_active"]) == ("INACTIVE", False)
    assert (on.json()["status"], on.json()["is_active"]) == ("ACTIVE", True)


async def test_unknown_employee_is_404(api, hr_world):
    r = await api.get(f"{EMP}/{uuid4()}", headers=hr_world.admin_a.headers)
    assert r.status_code == 404


# ── Tenant isolation / roles ────────────────────────────────


@pytest.fixture
async def staff_a(api, hr_world):
    r = await _staff(api, hr_world.admin_a, full_name="School A Clerk")
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize("actor", ["admin_b", "teacher_a", "teacher_b", "student_a", "anonymous"])
async def test_other_school_and_non_admins_cannot_touch_employees(api, hr_world, staff_a, actor):
    headers = {} if actor == "anonymous" else getattr(hr_world, actor).headers
    eid = staff_a["id"]
    calls = [
        ("GET", f"{EMP}/{eid}", None),
        ("PATCH", f"{EMP}/{eid}", {"full_name": "Hijacked"}),
        ("POST", f"{EMP}/{eid}/deactivate", None),
        ("POST", f"{EMP}/{eid}/activate", None),
    ]
    expected = {"admin_b": 404, "anonymous": 401}.get(actor, 403)

    for method, url, body in calls:
        r = await api.request(method, url, json=body, headers=headers)
        assert r.status_code == expected, (method, url, r.status_code, r.text)

    listing = await api.get(EMP, headers=headers)
    if actor == "admin_b":
        assert eid not in {row["id"] for row in listing.json()}
        assert all(row["school_id"] == str(hr_world.admin_b.school_id) for row in listing.json())
    else:
        assert listing.status_code == expected

    still = (await api.get(f"{EMP}/{eid}", headers=hr_world.admin_a.headers)).json()
    assert still["full_name"] == "School A Clerk"
    assert still["is_active"] is True


# ── Database constraints ────────────────────────────────────


@pytest.mark.parametrize(
    "fields",
    [
        {"staff_type": "NON_TEACHING", "full_name": None},
        {"staff_type": "TEACHING", "full_name": "X"},
        {"staff_type": "OTHER", "full_name": "X"},
        {"staff_type": "NON_TEACHING", "full_name": "X", "status": "GONE"},
        {"staff_type": "NON_TEACHING", "full_name": "X", "employment_type": "INTERN"},
    ],
)
async def test_db_rejects_invalid_employee_rows(db_session, hr_world, fields):
    row = Employee(school_id=hr_world.admin_a.school_id, status=fields.pop("status", "ACTIVE"), **fields)
    db_session.add(row)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


async def test_db_rejects_teacher_link_across_schools(db_session, hr_world):
    row = Employee(
        school_id=hr_world.admin_b.school_id, staff_type="TEACHING", teacher_id=hr_world.teacher_a.teacher_id
    )
    db_session.add(row)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()
