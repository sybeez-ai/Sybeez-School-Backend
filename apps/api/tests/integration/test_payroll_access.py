"""GET /payroll/access returns the effective portal role, including school grants."""

PAYROLL = "/api/v1/payroll"


async def test_access_for_admin_teacher_and_granted_approver(api, hr_world):
    admin = await api.get(f"{PAYROLL}/access", headers=hr_world.admin_a.headers)
    assert admin.status_code == 200, admin.text
    assert admin.json()["role"] == "payroll_admin"
    assert "runs.approve" in admin.json()["permissions"]

    teacher = await api.get(f"{PAYROLL}/access", headers=hr_world.teacher_a.headers)
    assert teacher.status_code == 200, teacher.text
    assert teacher.json() == {"role": "employee", "role_label": "Employee", "permissions": []}

    granted = await api.post(
        f"{PAYROLL}/roles",
        headers=hr_world.admin_a.headers,
        json={"user_id": str(hr_world.teacher_a2.user_id), "role": "PAYROLL_APPROVER", "reason": "Second signatory"},
    )
    assert granted.status_code == 201, granted.text
    approver = await api.get(f"{PAYROLL}/access", headers=hr_world.teacher_a2.headers)
    assert approver.status_code == 200, approver.text
    assert approver.json()["role"] == "payroll_approver"
    assert approver.json()["permissions"] == ["reports.view", "runs.approve", "runs.publish"]
