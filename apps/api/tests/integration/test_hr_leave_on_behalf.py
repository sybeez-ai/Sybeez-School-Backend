"""HR can record leave for an employee, optionally approving it in the same step."""

URL = "/api/v1/hr/leave/requests/on-behalf"


def _body(user_id, **extra):
    return {
        "user_id": str(user_id),
        "start_date": "2026-12-07",
        "end_date": "2026-12-08",
        "reason": "Phoned in sick",
        **extra,
    }


async def test_hr_records_pending_leave_for_employee(api, hr_world):
    r = await api.post(URL, json=_body(hr_world.teacher_a.user_id), headers=hr_world.admin_a.headers)

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "PENDING"
    assert r.json()["user_id"] == str(hr_world.teacher_a.user_id)


async def test_hr_records_and_approves_leave(api, hr_world):
    r = await api.post(
        URL, json=_body(hr_world.teacher_a.user_id, approve=True), headers=hr_world.admin_a.headers
    )

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "APPROVED"
    assert r.json()["decided_by"] == str(hr_world.admin_a.user_id)


async def test_teacher_cannot_record_leave_for_someone_else(api, hr_world):
    r = await api.post(URL, json=_body(hr_world.teacher_a2.user_id), headers=hr_world.teacher_a.headers)

    assert r.status_code == 403


async def test_hr_cannot_record_leave_for_other_school_employee(api, hr_world):
    r = await api.post(URL, json=_body(hr_world.teacher_b.user_id), headers=hr_world.admin_a.headers)

    assert r.status_code in (403, 404, 422), r.text
