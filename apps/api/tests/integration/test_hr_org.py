"""HRM Phase 3: departments, designations and employee assignment."""

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models import Department, Employee

DEPTS = "/api/v1/hr/departments"
DESIGS = "/api/v1/hr/designations"
EMP = "/api/v1/hr/employees"


async def _dept(api, actor, name="Academics", **extra):
    return await api.post(DEPTS, json={"name": name, **extra}, headers=actor.headers)


async def _desig(api, actor, name="HOD", **extra):
    return await api.post(DESIGS, json={"name": name, **extra}, headers=actor.headers)


async def _staff(api, actor, **extra):
    body = {"staff_type": "NON_TEACHING", "full_name": "Org Staff", **extra}
    r = await api.post(EMP, json=body, headers=actor.headers)
    assert r.status_code == 201, r.text
    return r.json()


# ── Departments ─────────────────────────────────────────────


async def test_department_crud(api, hr_world):
    a = hr_world.admin_a
    created = await _dept(api, a, "  Administration ", code="ADM", description="Front office")
    assert created.status_code == 201, created.text
    d = created.json()
    assert (d["name"], d["code"], d["is_active"], d["employee_count"]) == ("Administration", "ADM", True, 0)

    upd = await api.patch(f"{DEPTS}/{d['id']}", json={"description": "Office + accounts"}, headers=a.headers)
    assert upd.status_code == 200, upd.text
    assert upd.json()["description"] == "Office + accounts"

    listed = (await api.get(DEPTS, headers=a.headers)).json()
    assert [x["name"] for x in listed] == ["Administration"]

    gone = await api.delete(f"{DEPTS}/{d['id']}", headers=a.headers)
    assert gone.status_code == 204
    assert (await api.get(f"{DEPTS}/{d['id']}", headers=a.headers)).status_code == 404


async def test_department_name_unique_per_school_case_insensitive(api, hr_world):
    assert (await _dept(api, hr_world.admin_a, "Sports")).status_code == 201
    dup = await _dept(api, hr_world.admin_a, "sports")
    other_school = await _dept(api, hr_world.admin_b, "Sports")

    assert dup.status_code == 409, dup.text
    assert other_school.status_code == 201


async def test_rename_to_existing_name_is_409(api, hr_world):
    await _dept(api, hr_world.admin_a, "Science")
    maths = (await _dept(api, hr_world.admin_a, "Maths")).json()
    r = await api.patch(f"{DEPTS}/{maths['id']}", json={"name": "SCIENCE"}, headers=hr_world.admin_a.headers)
    assert r.status_code == 409


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "x"}, {"name": "A" * 121}, {"code": "C" * 33, "name": "Ok"}])
async def test_department_validation(api, hr_world, body):
    r = await api.post(DEPTS, json=body, headers=hr_world.admin_a.headers)
    assert r.status_code == 422, r.text


async def test_department_head_must_be_same_school(api, hr_world):
    own = await _staff(api, hr_world.admin_a, full_name="Head Person")
    other = await _staff(api, hr_world.admin_b, full_name="Other School")

    ok = await _dept(api, hr_world.admin_a, "Library", head_employee_id=own["id"])
    bad = await _dept(api, hr_world.admin_a, "Transport", head_employee_id=other["id"])

    assert ok.status_code == 201 and ok.json()["head_name"] == "Head Person"
    assert bad.status_code == 404, bad.text


async def test_filter_active_and_search(api, hr_world):
    a = hr_world.admin_a
    await _dept(api, a, "Accounts", code="ACC")
    old = (await _dept(api, a, "Old Wing")).json()
    await api.patch(f"{DEPTS}/{old['id']}", json={"is_active": False}, headers=a.headers)

    active = (await api.get(DEPTS, params={"active": "true"}, headers=a.headers)).json()
    inactive = (await api.get(DEPTS, params={"active": "false"}, headers=a.headers)).json()
    search = (await api.get(DEPTS, params={"q": "acc"}, headers=a.headers)).json()

    assert [d["name"] for d in active] == ["Accounts"]
    assert [d["name"] for d in inactive] == ["Old Wing"]
    assert [d["name"] for d in search] == ["Accounts"]


# ── Designations ────────────────────────────────────────────


async def test_designation_crud_and_uniqueness(api, hr_world):
    a = hr_world.admin_a
    d = (await _desig(api, a, "Accountant", description="Fee desk")).json()
    dup = await _desig(api, a, "ACCOUNTANT")
    upd = await api.patch(f"{DESIGS}/{d['id']}", json={"is_active": False}, headers=a.headers)
    gone = await api.delete(f"{DESIGS}/{d['id']}", headers=a.headers)

    assert dup.status_code == 409
    assert upd.status_code == 200 and upd.json()["is_active"] is False
    assert gone.status_code == 204


# ── Employee assignment ─────────────────────────────────────


async def test_assign_department_and_designation(api, hr_world):
    a = hr_world.admin_a
    dept = (await _dept(api, a, "Administration")).json()
    desig = (await _desig(api, a, "Clerk")).json()

    emp = await _staff(api, a, department_id=dept["id"], designation_id=desig["id"])

    assert (emp["department_name"], emp["designation_name"]) == ("Administration", "Clerk")
    counted = (await api.get(f"{DEPTS}/{dept['id']}", headers=a.headers)).json()
    assert counted["employee_count"] == 1
    by_dept = (await api.get(EMP, params={"department_id": dept["id"]}, headers=a.headers)).json()
    by_desig = (await api.get(EMP, params={"designation_id": desig["id"]}, headers=a.headers)).json()
    assert [e["id"] for e in by_dept] == [emp["id"]]
    assert [e["id"] for e in by_desig] == [emp["id"]]


async def test_assign_teaching_staff(api, hr_world):
    a = hr_world.admin_a
    dept = (await _dept(api, a, "Science")).json()
    rows = (await api.get(EMP, params={"staff_type": "TEACHING"}, headers=a.headers)).json()

    r = await api.patch(f"{EMP}/{rows[0]['id']}", json={"department_id": dept["id"]}, headers=a.headers)

    assert r.status_code == 200, r.text
    assert r.json()["department_name"] == "Science"


async def test_cannot_assign_other_school_department_or_designation(api, hr_world):
    b_dept = (await _dept(api, hr_world.admin_b, "B Dept")).json()
    b_desig = (await _desig(api, hr_world.admin_b, "B Role")).json()
    emp = await _staff(api, hr_world.admin_a)

    r1 = await api.patch(f"{EMP}/{emp['id']}", json={"department_id": b_dept["id"]}, headers=hr_world.admin_a.headers)
    r2 = await api.patch(f"{EMP}/{emp['id']}", json={"designation_id": b_desig["id"]}, headers=hr_world.admin_a.headers)
    r3 = await api.post(
        EMP,
        json={"staff_type": "NON_TEACHING", "full_name": "Sneaky", "department_id": b_dept["id"]},
        headers=hr_world.admin_a.headers,
    )

    assert (r1.status_code, r2.status_code, r3.status_code) == (404, 404, 404)


async def test_cannot_assign_inactive_department_but_existing_assignment_survives(api, hr_world):
    a = hr_world.admin_a
    dept = (await _dept(api, a, "Closing")).json()
    emp = await _staff(api, a, department_id=dept["id"])
    await api.patch(f"{DEPTS}/{dept['id']}", json={"is_active": False}, headers=a.headers)

    new_emp = await api.post(
        EMP, json={"staff_type": "NON_TEACHING", "full_name": "Late", "department_id": dept["id"]}, headers=a.headers
    )
    keep = await api.patch(f"{EMP}/{emp['id']}", json={"notes": "still here", "department_id": dept["id"]}, headers=a.headers)

    assert new_emp.status_code == 422, new_emp.text
    assert keep.status_code == 200 and keep.json()["department_name"] == "Closing"


async def test_delete_blocked_while_assigned_then_allowed_after_unassign(api, hr_world):
    a = hr_world.admin_a
    dept = (await _dept(api, a, "Transport")).json()
    emp = await _staff(api, a, department_id=dept["id"])

    blocked = await api.delete(f"{DEPTS}/{dept['id']}", headers=a.headers)
    await api.patch(f"{EMP}/{emp['id']}", json={"department_id": None}, headers=a.headers)
    allowed = await api.delete(f"{DEPTS}/{dept['id']}", headers=a.headers)

    assert blocked.status_code == 409
    assert "1 employee is assigned" in blocked.json()["error"]["message"]
    assert allowed.status_code == 204


# ── Isolation / roles ───────────────────────────────────────


@pytest.mark.parametrize("actor", ["admin_b", "teacher_a", "student_a", "anonymous"])
async def test_other_school_and_non_admins_cannot_touch_org(api, hr_world, actor):
    dept = (await _dept(api, hr_world.admin_a, "School A Dept")).json()
    desig = (await _desig(api, hr_world.admin_a, "School A Role")).json()
    headers = {} if actor == "anonymous" else getattr(hr_world, actor).headers
    expected = {"admin_b": 404, "anonymous": 401}.get(actor, 403)

    for url in (f"{DEPTS}/{dept['id']}", f"{DESIGS}/{desig['id']}"):
        assert (await api.get(url, headers=headers)).status_code == expected
        assert (await api.patch(url, json={"name": "Hijack"}, headers=headers)).status_code == expected
        assert (await api.delete(url, headers=headers)).status_code == expected

    for url in (DEPTS, DESIGS):
        r = await api.get(url, headers=headers)
        if actor == "admin_b":
            assert r.status_code == 200 and r.json() == []
        else:
            assert r.status_code == expected

    still = (await api.get(f"{DEPTS}/{dept['id']}", headers=hr_world.admin_a.headers)).json()
    assert still["name"] == "School A Dept"


# ── Database constraints ────────────────────────────────────


async def test_db_rejects_cross_school_employee_department(db_session, hr_world):
    dept = Department(school_id=hr_world.admin_b.school_id, name=f"B-{uuid4().hex[:6]}", is_active=True)
    db_session.add(dept)
    await db_session.flush()
    emp = Employee(
        school_id=hr_world.admin_a.school_id, staff_type="NON_TEACHING", full_name="X", department_id=dept.id
    )
    db_session.add(emp)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


async def test_db_rejects_duplicate_department_name(db_session, hr_world):
    db_session.add(Department(school_id=hr_world.admin_a.school_id, name="Dup", is_active=True))
    await db_session.flush()
    db_session.add(Department(school_id=hr_world.admin_a.school_id, name="DUP", is_active=True))
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()
