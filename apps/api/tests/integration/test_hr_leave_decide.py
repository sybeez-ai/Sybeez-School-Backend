"""Regression: POST /hr/leave/requests/{id}/decide returned 500 (MissingGreenlet on updated_at)."""

from datetime import datetime

BASE = "/api/v1/hr/leave/requests"


async def _create_leave(api, teacher, reason="Family function"):
    r = await api.post(
        BASE,
        json={"start_date": "2026-11-02", "end_date": "2026-11-03", "reason": reason},
        headers=teacher.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _status_in_admin_list(api, admin, leave_id):
    r = await api.get(BASE, headers=admin.headers)
    assert r.status_code == 200, r.text
    return next((x["status"] for x in r.json() if x["id"] == leave_id), None)


async def test_admin_approves_pending_leave(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/decide", json={"decision": "APPROVED"}, headers=hr_world.admin_a.headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPROVED"
    assert body["decided_by"] == str(hr_world.admin_a.user_id)
    assert body["decided_at"] is not None
    assert datetime.fromisoformat(body["updated_at"]) >= datetime.fromisoformat(body["created_at"])
    assert await _status_in_admin_list(api, hr_world.admin_a, leave["id"]) == "APPROVED"


async def test_admin_rejects_pending_leave_with_note(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(
        f"{BASE}/{leave['id']}/decide",
        json={"decision": "REJECTED", "decision_note": "Exam week"},
        headers=hr_world.admin_a.headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"
    assert r.json()["decision_note"] == "Exam week"
    assert await _status_in_admin_list(api, hr_world.admin_a, leave["id"]) == "REJECTED"


async def test_deciding_twice_is_rejected(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)
    url = f"{BASE}/{leave['id']}/decide"
    assert (await api.post(url, json={"decision": "APPROVED"}, headers=hr_world.admin_a.headers)).status_code == 200

    r = await api.post(url, json={"decision": "REJECTED"}, headers=hr_world.admin_a.headers)

    assert r.status_code == 422
    assert await _status_in_admin_list(api, hr_world.admin_a, leave["id"]) == "APPROVED"


async def test_invalid_decision_value_is_422(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/decide", json={"decision": "MAYBE"}, headers=hr_world.admin_a.headers)

    assert r.status_code == 422
    assert await _status_in_admin_list(api, hr_world.admin_a, leave["id"]) == "PENDING"


async def test_teacher_cannot_decide(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/decide", json={"decision": "APPROVED"}, headers=hr_world.teacher_a.headers)

    assert r.status_code == 403
    assert await _status_in_admin_list(api, hr_world.admin_a, leave["id"]) == "PENDING"


async def test_unauthenticated_decide_is_401(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/decide", json={"decision": "APPROVED"})

    assert r.status_code == 401


async def test_admin_of_other_school_cannot_decide(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/decide", json={"decision": "APPROVED"}, headers=hr_world.admin_b.headers)

    assert r.status_code == 404
    assert await _status_in_admin_list(api, hr_world.admin_a, leave["id"]) == "PENDING"
    assert await _status_in_admin_list(api, hr_world.admin_b, leave["id"]) is None


async def test_decide_unknown_leave_is_404(api, hr_world):
    r = await api.post(
        f"{BASE}/00000000-0000-0000-0000-000000000000/decide",
        json={"decision": "APPROVED"},
        headers=hr_world.admin_a.headers,
    )

    assert r.status_code == 404
