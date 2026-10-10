"""Regression: POST /hr/leave/requests/{id}/cancel returned 500 (MissingGreenlet on updated_at)."""

from datetime import datetime

BASE = "/api/v1/hr/leave/requests"


async def _create_leave(api, teacher):
    r = await api.post(
        BASE,
        json={"start_date": "2026-11-02", "end_date": "2026-11-03", "reason": "Medical appointment"},
        headers=teacher.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _my_status(api, teacher, leave_id):
    r = await api.get(f"{BASE}/me", headers=teacher.headers)
    assert r.status_code == 200, r.text
    return next((x["status"] for x in r.json() if x["id"] == leave_id), None)


async def test_teacher_cancels_own_pending_leave(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/cancel", headers=hr_world.teacher_a.headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "CANCELLED"
    assert datetime.fromisoformat(body["updated_at"]) >= datetime.fromisoformat(body["created_at"])
    assert await _my_status(api, hr_world.teacher_a, leave["id"]) == "CANCELLED"


async def test_cancelled_leave_cannot_then_be_decided(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)
    assert (await api.post(f"{BASE}/{leave['id']}/cancel", headers=hr_world.teacher_a.headers)).status_code == 200

    r = await api.post(f"{BASE}/{leave['id']}/decide", json={"decision": "APPROVED"}, headers=hr_world.admin_a.headers)

    assert r.status_code == 422


async def test_cannot_cancel_twice(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)
    url = f"{BASE}/{leave['id']}/cancel"
    assert (await api.post(url, headers=hr_world.teacher_a.headers)).status_code == 200

    r = await api.post(url, headers=hr_world.teacher_a.headers)

    assert r.status_code == 422


async def test_cannot_cancel_decided_leave(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)
    decided = await api.post(f"{BASE}/{leave['id']}/decide", json={"decision": "APPROVED"}, headers=hr_world.admin_a.headers)
    assert decided.status_code == 200, decided.text

    r = await api.post(f"{BASE}/{leave['id']}/cancel", headers=hr_world.teacher_a.headers)

    assert r.status_code == 422
    assert await _my_status(api, hr_world.teacher_a, leave["id"]) == "APPROVED"


async def test_colleague_in_same_school_cannot_cancel(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/cancel", headers=hr_world.teacher_a2.headers)

    assert r.status_code == 403
    assert await _my_status(api, hr_world.teacher_a, leave["id"]) == "PENDING"


async def test_teacher_of_other_school_cannot_cancel(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/cancel", headers=hr_world.teacher_b.headers)

    assert r.status_code == 404
    assert await _my_status(api, hr_world.teacher_a, leave["id"]) == "PENDING"


async def test_admin_cannot_use_teacher_cancel_endpoint(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/cancel", headers=hr_world.admin_a.headers)

    assert r.status_code == 403
    assert await _my_status(api, hr_world.teacher_a, leave["id"]) == "PENDING"


async def test_unauthenticated_cancel_is_401(api, hr_world):
    leave = await _create_leave(api, hr_world.teacher_a)

    r = await api.post(f"{BASE}/{leave['id']}/cancel")

    assert r.status_code == 401


async def test_cancel_unknown_leave_is_404(api, hr_world):
    r = await api.post(f"{BASE}/00000000-0000-0000-0000-000000000000/cancel", headers=hr_world.teacher_a.headers)

    assert r.status_code == 404
