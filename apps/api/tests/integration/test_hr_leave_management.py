"""HRM Phase 5: leave details, filters, validation and the teacher/admin flow."""

import pytest

BASE = "/api/v1/hr/leave/requests"
DEPTS = "/api/v1/hr/departments"
EMP = "/api/v1/hr/employees"


async def _apply(
    api, teacher, start="2026-11-02", end="2026-11-03", reason="Medical appointment"
):
    return await api.post(
        BASE,
        json={"start_date": start, "end_date": end, "reason": reason},
        headers=teacher.headers,
    )


async def _decide(api, admin, leave_id, decision="APPROVED", note=None):
    body = {
        "decision": decision,
        **({"decision_note": note} if note is not None else {}),
    }
    return await api.post(f"{BASE}/{leave_id}/decide", json=body, headers=admin.headers)


async def test_admin_list_shows_names_codes_department_and_days(api, hr_world):
    a, t = hr_world.admin_a, hr_world.teacher_a
    dept = (await api.post(DEPTS, json={"name": "Science"}, headers=a.headers)).json()
    emps = (
        await api.get(EMP, params={"staff_type": "TEACHING"}, headers=a.headers)
    ).json()
    emp = next(e for e in emps if e["teacher_id"] == str(t.teacher_id))
    await api.patch(
        f"{EMP}/{emp['id']}", json={"department_id": dept["id"]}, headers=a.headers
    )

    created = await _apply(api, t, "2026-11-02", "2026-11-04")
    assert created.status_code == 201, created.text
    leave = created.json()
    assert (leave["staff_name"], leave["days"], leave["department_name"]) == (
        emp["full_name"],
        3,
        "Science",
    )
    assert leave["employee_code"] == emp["employee_code"]

    listed = next(
        x
        for x in (await api.get(BASE, headers=a.headers)).json()
        if x["id"] == leave["id"]
    )
    assert listed["staff_name"] == emp["full_name"]


async def test_decision_records_note_and_decider_name(api, hr_world):
    leave = (await _apply(api, hr_world.teacher_a)).json()

    r = await _decide(
        api, hr_world.admin_a, leave["id"], "REJECTED", "  Exams that week  "
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["decision_note"]) == ("REJECTED", "Exams that week")
    assert body["decided_by"] == str(hr_world.admin_a.user_id)
    assert body["decided_by_name"]
    mine = next(
        x
        for x in (
            await api.get(f"{BASE}/me", headers=hr_world.teacher_a.headers)
        ).json()
        if x["id"] == leave["id"]
    )
    assert (mine["status"], mine["decision_note"], mine["decided_by_name"]) == (
        "REJECTED",
        "Exams that week",
        body["decided_by_name"],
    )


async def test_cannot_redecide(api, hr_world):
    leave = (await _apply(api, hr_world.teacher_a)).json()
    assert (
        await _decide(api, hr_world.admin_a, leave["id"], "APPROVED")
    ).status_code == 200

    again = await _decide(api, hr_world.admin_a, leave["id"], "REJECTED")

    assert again.status_code == 422
    mine = (await api.get(f"{BASE}/me", headers=hr_world.teacher_a.headers)).json()
    assert next(x["status"] for x in mine if x["id"] == leave["id"]) == "APPROVED"


@pytest.mark.parametrize(
    "body",
    [
        {"start_date": "2026-11-05", "end_date": "2026-11-04", "reason": "Backwards"},
        {"start_date": "2026-01-01", "end_date": "2027-01-05", "reason": "Too long"},
        {"start_date": "2026-11-31", "end_date": "2026-12-01", "reason": "Bad date"},
        {"start_date": "2026-11-05", "end_date": "2026-11-05", "reason": "  x "},
        {"start_date": "2026-11-05", "reason": "Missing end"},
    ],
)
async def test_invalid_leave_requests_rejected(api, hr_world, body):
    r = await api.post(BASE, json=body, headers=hr_world.teacher_a.headers)
    assert r.status_code == 422, r.text


async def test_overlapping_pending_or_approved_leave_is_409(api, hr_world):
    t = hr_world.teacher_a
    first = (await _apply(api, t, "2026-12-01", "2026-12-05")).json()

    overlap_pending = await _apply(api, t, "2026-12-05", "2026-12-06")
    await _decide(api, hr_world.admin_a, first["id"], "APPROVED")
    overlap_approved = await _apply(api, t, "2026-11-30", "2026-12-01")
    adjacent = await _apply(api, t, "2026-12-07", "2026-12-07")
    colleague = await _apply(api, hr_world.teacher_a2, "2026-12-01", "2026-12-05")

    assert overlap_pending.status_code == 409
    assert "pending leave request" in overlap_pending.json()["error"]["message"]
    assert overlap_approved.status_code == 409
    assert adjacent.status_code == 201
    assert colleague.status_code == 201


async def test_rejected_or_cancelled_leave_does_not_block_new_request(api, hr_world):
    t = hr_world.teacher_a
    rejected = (await _apply(api, t, "2027-01-04", "2027-01-05")).json()
    await _decide(api, hr_world.admin_a, rejected["id"], "REJECTED")
    cancelled = (await _apply(api, t, "2027-01-06", "2027-01-07")).json()
    await api.post(f"{BASE}/{cancelled['id']}/cancel", headers=t.headers)

    again = await _apply(api, t, "2027-01-04", "2027-01-07")

    assert again.status_code == 201, again.text


async def test_admin_filters(api, hr_world):
    a = hr_world.admin_a
    l1 = (
        await _apply(
            api, hr_world.teacher_a, "2027-02-01", "2027-02-02", "Family wedding"
        )
    ).json()
    l2 = (
        await _apply(api, hr_world.teacher_a2, "2027-03-10", "2027-03-10", "Dentist")
    ).json()
    await _decide(api, a, l2["id"], "APPROVED")

    async def ids(**params):
        r = await api.get(BASE, params=params, headers=a.headers)
        assert r.status_code == 200, r.text
        return {x["id"] for x in r.json()}

    assert l1["id"] in await ids(status="pending") and l2["id"] not in await ids(
        status="PENDING"
    )
    assert await ids(q="wedding") >= {l1["id"]} and l2["id"] not in await ids(
        q="wedding"
    )
    feb = await ids(date_from="2027-02-02", date_to="2027-02-28")
    assert l1["id"] in feb and l2["id"] not in feb
    assert await ids(user_id=str(hr_world.teacher_a2.user_id)) == {l2["id"]}

    assert (
        await api.get(BASE, params={"status": "BOGUS"}, headers=a.headers)
    ).status_code == 422
    bad_range = await api.get(
        BASE,
        params={"date_from": "2027-03-01", "date_to": "2027-02-01"},
        headers=a.headers,
    )
    assert bad_range.status_code == 422


async def test_decision_note_length_limited(api, hr_world):
    leave = (await _apply(api, hr_world.teacher_a)).json()
    r = await _decide(api, hr_world.admin_a, leave["id"], "APPROVED", "x" * 2001)
    assert r.status_code == 422


@pytest.mark.parametrize("actor", ["teacher_a", "teacher_b", "student_a", "anonymous"])
async def test_only_school_admin_decides(api, hr_world, actor):
    leave = (await _apply(api, hr_world.teacher_a)).json()
    headers = {} if actor == "anonymous" else getattr(hr_world, actor).headers

    r = await _decide(api, type("H", (), {"headers": headers}), leave["id"], "APPROVED")

    assert r.status_code == (401 if actor == "anonymous" else 403)
    mine = (await api.get(f"{BASE}/me", headers=hr_world.teacher_a.headers)).json()
    assert next(x["status"] for x in mine if x["id"] == leave["id"]) == "PENDING"


async def test_other_school_admin_cannot_see_or_decide(api, hr_world):
    leave = (await _apply(api, hr_world.teacher_a)).json()

    decide = await _decide(api, hr_world.admin_b, leave["id"], "REJECTED")
    listed = (await api.get(BASE, headers=hr_world.admin_b.headers)).json()
    by_user = (
        await api.get(
            BASE,
            params={"user_id": str(hr_world.teacher_a.user_id)},
            headers=hr_world.admin_b.headers,
        )
    ).json()

    assert decide.status_code == 404
    assert leave["id"] not in {x["id"] for x in listed}
    assert by_user == []


async def test_teacher_sees_only_own_requests(api, hr_world):
    mine = (await _apply(api, hr_world.teacher_a, "2027-04-01", "2027-04-01")).json()
    theirs = (await _apply(api, hr_world.teacher_a2, "2027-04-01", "2027-04-01")).json()

    listed = {
        x["id"]
        for x in (
            await api.get(f"{BASE}/me", headers=hr_world.teacher_a.headers)
        ).json()
    }

    assert mine["id"] in listed and theirs["id"] not in listed
    assert (await api.get(BASE, headers=hr_world.teacher_a.headers)).status_code == 403
