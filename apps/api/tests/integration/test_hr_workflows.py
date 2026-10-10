"""Phase 7: onboarding and offboarding workflows (real FastAPI + PostgreSQL)."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.db.models import HrWorkflow, HrWorkflowTask

API = "/api/v1/hr"
WF = f"{API}/workflows"


async def _employee_id(api, actor, admin):
    rows = (await api.get(f"{API}/employees", headers=admin.headers)).json()
    return next(r["id"] for r in rows if r.get("teacher_id") == str(actor.teacher_id))


async def _start(api, admin, emp, **extra):
    body = {
        "employee_id": emp,
        "workflow_type": "ONBOARDING",
        "start_date": "2026-10-01",
        **extra,
    }
    return await api.post(WF, headers=admin.headers, json=body)


async def test_start_toggle_complete_lifecycle(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)

    tpl = await api.get(
        f"{WF}/template",
        params={"workflow_type": "OFFBOARDING"},
        headers=w.admin_a.headers,
    )
    assert tpl.status_code == 200 and "Conduct exit interview" in tpl.json()["tasks"]

    r = await _start(
        api,
        w.admin_a,
        emp,
        expected_completion_date="2026-10-15",
        notes="New maths teacher",
    )
    assert r.status_code == 201, r.text
    wf = r.json()
    assert wf["status"] == "OPEN"
    assert wf["total_tasks"] == 5 and wf["done_tasks"] == 0
    assert wf["owner_user_id"] == str(w.admin_a.user_id)
    assert wf["owner_name"] == "IT SCHOOL_ADMIN"
    assert [t["sort_order"] for t in wf["tasks"]] == [10, 20, 30, 40, 50]

    dup = await _start(api, w.admin_a, emp)
    assert dup.status_code == 409
    assert "already has an active onboarding" in dup.json()["error"]["message"]

    off = await _start(api, w.admin_a, emp, workflow_type="OFFBOARDING")
    assert off.status_code == 201

    early = await api.post(f"{WF}/{wf['id']}/complete", headers=w.admin_a.headers)
    assert (
        early.status_code == 422 and "0 of 5 done" in early.json()["error"]["message"]
    )

    first = wf["tasks"][0]["id"]
    t = await api.post(
        f"{WF}/{wf['id']}/tasks/{first}/toggle",
        headers=w.admin_a.headers,
        json={"done": True},
    )
    assert t.status_code == 200
    body = t.json()
    assert body["status"] == "IN_PROGRESS" and body["done_tasks"] == 1
    assert body["tasks"][0]["completed_by_name"] == "IT SCHOOL_ADMIN"
    assert body["tasks"][0]["completed_at"]

    undo = await api.post(
        f"{WF}/{wf['id']}/tasks/{first}/toggle",
        headers=w.admin_a.headers,
        json={"done": False},
    )
    assert undo.json()["status"] == "OPEN"
    assert undo.json()["tasks"][0]["completed_at"] is None

    for task in wf["tasks"]:
        await api.post(
            f"{WF}/{wf['id']}/tasks/{task['id']}/toggle",
            headers=w.admin_a.headers,
            json={"done": True},
        )
    done = await api.post(f"{WF}/{wf['id']}/complete", headers=w.admin_a.headers)
    assert done.status_code == 200
    assert (
        done.json()["status"] == "COMPLETED"
        and done.json()["completed_by_name"] == "IT SCHOOL_ADMIN"
    )

    locked = await api.post(
        f"{WF}/{wf['id']}/tasks/{first}/toggle",
        headers=w.admin_a.headers,
        json={"done": False},
    )
    assert (
        locked.status_code == 422 and "completed" in locked.json()["error"]["message"]
    )
    assert (
        await api.patch(
            f"{WF}/{wf['id']}", headers=w.admin_a.headers, json={"notes": "x"}
        )
    ).status_code == 422

    again = await _start(api, w.admin_a, emp)
    assert again.status_code == 201, "a completed workflow no longer blocks a new one"


async def test_tasks_crud_update_and_cancel(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    r = await _start(
        api,
        w.admin_a,
        emp,
        tasks=[
            {"title": "Sign contract", "due_date": "2026-10-02"},
            {"title": "Laptop"},
        ],
    )
    wf = r.json()
    assert [t["title"] for t in wf["tasks"]] == ["Sign contract", "Laptop"]
    assert wf["tasks"][0]["is_overdue"] is True
    assert wf["overdue_tasks"] == 1

    add = await api.post(
        f"{WF}/{wf['id']}/tasks",
        headers=w.admin_a.headers,
        json={
            "title": "  Meet the principal  ",
            "assignee_user_id": str(w.teacher_a.user_id),
        },
    )
    assert add.status_code == 201
    new = add.json()["tasks"][-1]
    assert new["title"] == "Meet the principal" and new["sort_order"] == 30
    assert new["assignee_name"] == "IT TEACHER"

    bad_assignee = await api.post(
        f"{WF}/{wf['id']}/tasks",
        headers=w.admin_a.headers,
        json={"title": "x", "assignee_user_id": str(w.teacher_a2.user_id)},
    )
    assert bad_assignee.status_code == 422

    upd = await api.patch(
        f"{WF}/{wf['id']}/tasks/{new['id']}",
        headers=w.admin_a.headers,
        json={"title": "Meet principal", "due_date": None, "description": "Room 4"},
    )
    assert upd.status_code == 200
    assert (
        next(t for t in upd.json()["tasks"] if t["id"] == new["id"])["description"]
        == "Room 4"
    )

    meta = await api.patch(
        f"{WF}/{wf['id']}",
        headers=w.admin_a.headers,
        json={"expected_completion_date": "2026-09-01"},
    )
    assert meta.status_code == 422
    meta = await api.patch(
        f"{WF}/{wf['id']}",
        headers=w.admin_a.headers,
        json={"expected_completion_date": "2026-10-20", "notes": "Starts Monday"},
    )
    assert meta.status_code == 200 and meta.json()["notes"] == "Starts Monday"
    assert (
        await api.patch(
            f"{WF}/{wf['id']}",
            headers=w.admin_a.headers,
            json={"owner_user_id": str(w.teacher_a.user_id)},
        )
    ).status_code == 422

    for task in upd.json()["tasks"][:2]:
        d = await api.delete(
            f"{WF}/{wf['id']}/tasks/{task['id']}", headers=w.admin_a.headers
        )
        assert d.status_code == 200
    last = await api.delete(
        f"{WF}/{wf['id']}/tasks/{new['id']}", headers=w.admin_a.headers
    )
    assert last.status_code == 422

    assert (
        await api.post(
            f"{WF}/{wf['id']}/cancel", headers=w.admin_a.headers, json={"reason": " "}
        )
    ).status_code == 422
    c = await api.post(
        f"{WF}/{wf['id']}/cancel",
        headers=w.admin_a.headers,
        json={"reason": "Candidate declined"},
    )
    assert c.status_code == 200
    assert (
        c.json()["status"] == "CANCELLED"
        and c.json()["cancel_reason"] == "Candidate declined"
    )
    assert (
        await api.post(
            f"{WF}/{wf['id']}/cancel", headers=w.admin_a.headers, json={"reason": "x"}
        )
    ).status_code == 422


async def test_validation_and_filters(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    bad_dates = await _start(api, w.admin_a, emp, expected_completion_date="2026-09-01")
    assert bad_dates.status_code == 422
    no_tasks = await _start(api, w.admin_a, emp, tasks=[])
    assert no_tasks.status_code == 422
    bad_type = await _start(api, w.admin_a, emp, workflow_type="TRANSFER")
    assert bad_type.status_code == 422
    missing = await _start(api, w.admin_a, str(uuid4()))
    assert missing.status_code == 404

    a = (await _start(api, w.admin_a, emp)).json()
    emp2 = await _employee_id(api, w.teacher_a2, w.admin_a)
    b = (await _start(api, w.admin_a, emp2, workflow_type="OFFBOARDING")).json()

    def ids(resp):
        return {x["id"] for x in resp.json()}

    assert ids(
        await api.get(
            WF, headers=w.admin_a.headers, params={"workflow_type": "offboarding"}
        )
    ) == {b["id"]}
    assert ids(
        await api.get(WF, headers=w.admin_a.headers, params={"status": "ACTIVE"})
    ) == {a["id"], b["id"]}
    assert ids(
        await api.get(WF, headers=w.admin_a.headers, params={"employee_id": emp})
    ) == {a["id"]}
    assert (
        await api.get(WF, headers=w.admin_a.headers, params={"status": "BOGUS"})
    ).status_code == 422
    listed = (await api.get(WF, headers=w.admin_a.headers)).json()
    assert all(x["tasks"] is None for x in listed)

    owners = (await api.get(f"{WF}/owners", headers=w.admin_a.headers)).json()
    assert [o["user_id"] for o in owners] == [str(w.admin_a.user_id)]


async def test_access_matrix_and_isolation(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    wf = (await _start(api, w.admin_a, emp)).json()
    task = wf["tasks"][0]["id"]

    for actor in (w.admin_b,):
        assert (
            await api.get(f"{WF}/{wf['id']}", headers=actor.headers)
        ).status_code == 404
        assert (
            await api.patch(
                f"{WF}/{wf['id']}", headers=actor.headers, json={"notes": "x"}
            )
        ).status_code == 404
        assert (
            await api.post(
                f"{WF}/{wf['id']}/tasks/{task}/toggle",
                headers=actor.headers,
                json={"done": True},
            )
        ).status_code == 404
        assert (
            await api.post(f"{WF}/{wf['id']}/complete", headers=actor.headers)
        ).status_code == 404
        assert wf["id"] not in {
            x["id"] for x in (await api.get(WF, headers=actor.headers)).json()
        }
        assert (await _start(api, actor, emp)).status_code == 404

    for actor in (w.teacher_a, w.teacher_b, w.student_a):
        assert (await api.get(WF, headers=actor.headers)).status_code == 403
        assert (
            await api.get(f"{WF}/{wf['id']}", headers=actor.headers)
        ).status_code == 403
        assert (await _start(api, actor, emp)).status_code == 403
    assert (await api.get(WF)).status_code == 401
    assert (await api.get(f"{API}/me/workflows")).status_code == 401
    assert (
        await api.get(f"{API}/me/workflows", headers=w.student_a.headers)
    ).status_code == 403

    mine = (await api.get(f"{API}/me/workflows", headers=w.teacher_a.headers)).json()
    assert [m["id"] for m in mine] == [wf["id"]] and len(mine[0]["tasks"]) == 5
    assert (
        await api.get(f"{API}/me/workflows", headers=w.teacher_a2.headers)
    ).json() == []
    assert (
        await api.get(f"{API}/me/workflows", headers=w.teacher_b.headers)
    ).json() == []


async def test_db_constraints(api, hr_world, db_session):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    wf_id = (await _start(api, w.admin_a, emp)).json()["id"]
    wf = (
        await db_session.execute(select(HrWorkflow).where(HrWorkflow.id == wf_id))
    ).scalar_one()

    for stmt in (
        "UPDATE hr_workflows SET status = 'COMPLETED' WHERE id = :id",
        "UPDATE hr_workflows SET status = 'WEIRD' WHERE id = :id",
        "UPDATE hr_workflows SET expected_completion_date = start_date - 1 WHERE id = :id",
        "UPDATE hr_workflows SET school_id = :other WHERE id = :id",
        "UPDATE hr_workflow_tasks SET is_done = TRUE WHERE workflow_id = :id",
    ):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(stmt), {"id": wf_id, "other": w.admin_b.school_id}
                )

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                HrWorkflow(
                    school_id=wf.school_id,
                    employee_id=wf.employee_id,
                    workflow_type="ONBOARDING",
                    status="IN_PROGRESS",
                    start_date=wf.start_date,
                )
            )
            await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                HrWorkflowTask(
                    school_id=w.admin_b.school_id,
                    workflow_id=wf.id,
                    title="cross-school",
                )
            )
            await db_session.flush()
