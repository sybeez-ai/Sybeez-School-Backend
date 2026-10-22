"""The student portal asks whether fee viewing is on instead of calling /fees/invoices and getting 403."""


async def test_student_sees_fee_viewing_off_by_default(api, hr_world):
    r = await api.get("/api/v1/fees/student-access", headers=hr_world.student_a.headers)

    assert r.status_code == 200, r.text
    assert r.json() == {"student_fee_view": False}


async def test_teacher_cannot_read_student_fee_access(api, hr_world):
    r = await api.get("/api/v1/fees/student-access", headers=hr_world.teacher_a.headers)

    assert r.status_code == 403
