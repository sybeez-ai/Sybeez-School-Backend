"""Staff read their shift timings without payroll access (was 403 on /payroll/settings)."""


async def test_teacher_reads_own_shift(api, hr_world):
    r = await api.get("/api/v1/hr/attendance/shift/me", headers=hr_world.teacher_a.headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"shift_start", "shift_end", "grace_minutes", "geofence_enabled", "geofence_radius_m"}
    assert "geofence_lat" not in body


async def test_student_cannot_read_staff_shift(api, hr_world):
    r = await api.get("/api/v1/hr/attendance/shift/me", headers=hr_world.student_a.headers)

    assert r.status_code == 403
