"""HR employees are the staff identity. Access, payslips, and the admin directory use that row."""

from datetime import date
from uuid import uuid4

from sqlalchemy import select

from src.core.security import create_access_token
from src.db.models import Employee, Invitation, PayrollPayslip, SchoolMembership, Teacher, User
from src.shared.enums import UserRole

EMP = "/api/v1/hr/employees"
PAYROLL = "/api/v1/payroll"
STAFF = "/api/v1/staff/me/payslips"
ADMIN = "/api/v1/admin/staff"
RUNS = "/api/v1/hr/payroll/runs"


async def _approver(db, school_id):
    user = User(id=uuid4(), email=f"it-{uuid4().hex[:12]}@example.com", full_name="Payroll approver")
    db.add(user)
    await db.flush()
    db.add(SchoolMembership(user_id=user.id, school_id=school_id, role=UserRole.SCHOOL_ADMIN.value))
    await db.flush()
    token = create_access_token(subject=user.id, role=UserRole.SCHOOL_ADMIN.value, school_id=school_id)
    return {"Authorization": f"Bearer {token}"}


async def test_grant_access_provisions_staff_login_and_invite(api, hr_world, db_session):
    email = f"it-{uuid4().hex[:10]}@example.com"
    created = await api.post(
        EMP,
        headers=hr_world.admin_a.headers,
        json={
            "staff_type": "NON_TEACHING",
            "full_name": "Leela Librarian",
            "email": email,
            "employee_code": f"NT-{uuid4().hex[:6]}",
            "employment_type": "FULL_TIME",
            "grant_access": True,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_login"] is True
    assert body["user_id"]
    assert body["temporary_password"]
    assert body["invite_token"]

    user = await db_session.get(User, body["user_id"])
    assert user.email == email
    assert user.password_hash
    membership = (
        await db_session.execute(
            select(SchoolMembership).where(
                SchoolMembership.user_id == user.id,
                SchoolMembership.school_id == hr_world.admin_a.school_id,
                SchoolMembership.role == UserRole.STAFF.value,
            )
        )
    ).scalar_one()
    assert membership.status == "ACTIVE"
    invite = (
        await db_session.execute(select(Invitation).where(Invitation.email == email))
    ).scalar_one()
    assert invite.role == UserRole.STAFF.value
    assert invite.status == "PENDING"

    signed_in = await api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": body["temporary_password"], "portal": "staff"},
    )
    assert signed_in.status_code == 200, signed_in.text
    token = signed_in.json()
    assert token["access_token"]
    assert token["role"] == "STAFF"
    assert token["school_id"] == str(hr_world.admin_a.school_id)

    wrong_portal = await api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": body["temporary_password"], "portal": "teacher"},
    )
    assert wrong_portal.status_code == 403


async def test_teaching_grant_access_links_teacher_user_and_employee(api, hr_world, db_session):
    email = f"it-{uuid4().hex[:10]}@example.com"
    created = await api.post(
        EMP,
        headers=hr_world.admin_a.headers,
        json={
            "staff_type": "TEACHING",
            "full_name": "Arun Physics",
            "email": email,
            "password": "StartPass123!",
            "grant_access": True,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    teacher = await db_session.get(Teacher, body["teacher_id"])
    employee = await db_session.get(Employee, body["id"])
    assert teacher.user_id == employee.user_id
    assert str(teacher.user_id) == body["user_id"]
    assert body["temporary_password"] == "StartPass123!"
    assert body["invite_token"]
    membership = (
        await db_session.execute(
            select(SchoolMembership).where(
                SchoolMembership.user_id == teacher.user_id,
                SchoolMembership.role == UserRole.TEACHER.value,
            )
        )
    ).scalar_one()
    assert membership.school_id == hr_world.admin_a.school_id

    signed_in = await api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "StartPass123!", "portal": "teacher"},
    )
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["role"] == "TEACHER"


async def test_published_payslip_is_visible_only_to_that_employee(api, db_session, hr_world):
    email = f"it-{uuid4().hex[:10]}@example.com"
    created = await api.post(
        EMP,
        headers=hr_world.admin_a.headers,
        json={
            "staff_type": "NON_TEACHING",
            "full_name": "Kiran Clerk",
            "email": email,
            "employee_code": f"NT-{uuid4().hex[:6]}",
            "grant_access": True,
        },
    )
    assert created.status_code == 201, created.text
    person = created.json()
    signed_in = await api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": person["temporary_password"], "portal": "staff"},
    )
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["must_change_password"] is True  # admin-issued temporary password
    tmp = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
    changed = await api.post(
        "/api/v1/auth/change-password",
        json={"current_password": person["temporary_password"], "new_password": "Own-Staff-Pass-9!"},
        headers=tmp,
    )
    assert changed.status_code == 200, changed.text
    signed_in = await api.post(
        "/api/v1/auth/login", json={"email": email, "password": "Own-Staff-Pass-9!", "portal": "staff"}
    )
    staff = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

    hidden = await api.get(STAFF, headers=staff)
    assert hidden.status_code == 200, hidden.text
    assert hidden.json() == []

    settings = await api.get(f"{PAYROLL}/settings", headers=hr_world.admin_a.headers)
    assert settings.status_code == 200, settings.text
    templates = await api.get(f"{PAYROLL}/salary-templates", headers=hr_world.admin_a.headers)
    template_id = templates.json()[0]["template"]["id"]
    assigned = await api.post(
        f"{PAYROLL}/salary-assignments",
        headers=hr_world.admin_a.headers,
        json={
            "employee_id": person["id"],
            "template_id": template_id,
            "effective_from": "2026-09-01",
            "overrides": {},
            "reason": "September salary",
        },
    )
    assert assigned.status_code == 201, assigned.text
    generated = await api.post(
        f"{PAYROLL}/runs/generate",
        headers=hr_world.admin_a.headers,
        json={"period_start": "2026-09-01", "period_end": "2026-09-30", "reason": "September payroll"},
    )
    assert generated.status_code == 201, generated.text
    run_id = generated.json()["run"]["id"]

    still_hidden = await api.get(STAFF, headers=staff)
    assert still_hidden.status_code == 200, still_hidden.text
    assert still_hidden.json() == []
    legacy_hidden = await api.get("/api/v1/hr/payroll/payslips/me", headers=staff)
    assert legacy_hidden.status_code == 200, legacy_hidden.text
    assert legacy_hidden.json() == []

    submitted = await api.post(
        f"{PAYROLL}/runs/{run_id}/submit",
        headers=hr_world.admin_a.headers,
        json={"reason": "Ready"},
    )
    assert submitted.status_code == 200, submitted.text
    approver = await _approver(db_session, hr_world.admin_a.school_id)
    approved = await api.post(
        f"{PAYROLL}/runs/{run_id}/approve",
        headers=approver,
        json={"reason": "Release payslips"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "PUBLISHED"

    visible = await api.get(STAFF, headers=staff)
    assert visible.status_code == 200, visible.text
    slips = visible.json()
    assert len(slips) == 1
    assert slips[0]["status"] == "PUBLISHED"
    assert slips[0]["employee_id"] == person["id"]
    detail = await api.get(f"{STAFF}/{slips[0]['id']}", headers=staff)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == slips[0]["id"]

    legacy = await api.get("/api/v1/hr/payroll/payslips/me", headers=staff)
    assert legacy.status_code == 200, legacy.text
    assert [row["id"] for row in legacy.json()] == [slips[0]["id"]]

    stored = await db_session.get(PayrollPayslip, slips[0]["id"])
    employee = await db_session.get(Employee, person["id"])
    assert stored.employee_id == employee.id
    assert str(employee.user_id) == person["user_id"]

    other = await api.get(STAFF, headers=hr_world.teacher_a.headers)
    assert other.status_code == 200, other.text
    assert slips[0]["id"] not in {row["id"] for row in other.json()}
    stolen = await api.get(f"{STAFF}/{slips[0]['id']}", headers=hr_world.teacher_a.headers)
    assert stolen.status_code == 404
    student = await api.get(STAFF, headers=hr_world.student_a.headers)
    assert student.status_code == 403
    other_school = await api.get(STAFF, headers=hr_world.teacher_b.headers)
    assert other_school.status_code == 200
    assert other_school.json() == []


async def test_hr_publish_links_payslip_to_the_employee(api, hr_world):
    listed = await api.get(EMP, headers=hr_world.admin_a.headers)
    teacher = next(row for row in listed.json() if row["teacher_id"] == str(hr_world.teacher_a.teacher_id))
    run = await api.post(
        RUNS,
        headers=hr_world.admin_a.headers,
        json={"label": "IT Oct identity", "period_start": "2026-10-01", "period_end": "2026-10-31"},
    )
    assert run.status_code == 201, run.text
    draft = await api.post(
        f"{RUNS}/{run.json()['id']}/payslips",
        headers=hr_world.admin_a.headers,
        json={"teacher_id": str(hr_world.teacher_a.teacher_id), "gross_amount": "30000.00", "deductions_amount": "1000.00"},
    )
    assert draft.status_code == 201, draft.text
    assert draft.json()["employee_id"] == teacher["id"]
    before = await api.get(STAFF, headers=hr_world.teacher_a.headers)
    assert before.status_code == 200
    assert draft.json()["id"] not in {row["id"] for row in before.json()}

    published = await api.patch(
        f"/api/v1/hr/payroll/payslips/{draft.json()['id']}",
        headers=hr_world.admin_a.headers,
        json={"status": "PUBLISHED"},
    )
    assert published.status_code == 200, published.text
    after = await api.get(STAFF, headers=hr_world.teacher_a.headers)
    assert after.status_code == 200, after.text
    mine = next(row for row in after.json() if row["id"] == draft.json()["id"])
    assert mine["status"] == "PUBLISHED"
    assert mine["employee_id"] == teacher["id"]
    assert str(hr_world.teacher_a.user_id) == teacher["user_id"]


async def test_school_admin_lists_staff_with_filters(api, hr_world):
    email = f"it-{uuid4().hex[:10]}@example.com"
    created = await api.post(
        EMP,
        headers=hr_world.admin_a.headers,
        json={
            "staff_type": "NON_TEACHING",
            "full_name": "Nalini Nurse",
            "email": email,
            "employee_code": f"NT-{uuid4().hex[:6]}",
            "employment_type": "CONTRACT",
            "grant_access": True,
        },
    )
    assert created.status_code == 201, created.text
    person = created.json()

    directory = await api.get(
        ADMIN,
        headers=hr_world.admin_a.headers,
        params={"staff_type": "NON_TEACHING", "q": "nalini", "has_login": True, "employment_type": "CONTRACT"},
    )
    assert directory.status_code == 200, directory.text
    page = directory.json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == person["id"]
    assert page["items"][0]["user_id"] == person["user_id"]
    assert page["items"][0]["has_login"] is True

    teaching = await api.get(ADMIN, headers=hr_world.admin_a.headers, params={"staff_type": "TEACHING"})
    assert teaching.status_code == 200, teaching.text
    assert teaching.json()["total"] >= 2
    assert all(row["staff_type"] == "TEACHING" for row in teaching.json()["items"])
    assert person["id"] not in {row["id"] for row in teaching.json()["items"]}

    dashboard = await api.get("/api/v1/admin/dashboard", headers=hr_world.admin_a.headers)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["teaching_staff"] >= 2
    assert dashboard.json()["non_teaching_staff"] >= 1

    for actor in (hr_world.teacher_a, hr_world.student_a):
        denied = await api.get(ADMIN, headers=actor.headers)
        assert denied.status_code == 403
    other = await api.get(ADMIN, headers=hr_world.admin_b.headers, params={"q": "nalini"})
    assert other.status_code == 200, other.text
    assert other.json()["total"] == 0

    today = date.today().isoformat()
    assert "fee_outstanding" in dashboard.json()
    assert today
