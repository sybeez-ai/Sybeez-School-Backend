"""HR operations: reports, probation, appraisals, hiring, training, roster, assets, exit."""

from uuid import uuid4

from sqlalchemy import select

from src.db.models import AuditLog

EMP = "/api/v1/hr/employees"
OPS = "/api/v1/hr"


async def _employee(api, admin) -> dict:
    response = await api.post(
        EMP,
        json={
            "staff_type": "NON_TEACHING",
            "full_name": "Ravi Kumar",
            "employee_code": f"NT-{uuid4().hex[:6]}",
            "joining_date": "2024-04-01",
            "employment_type": "FULL_TIME",
        },
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_reports_probation_appraisal_and_audit(api, hr_world, db_session):
    admin = hr_world.admin_a
    listed = await api.get(EMP, headers=admin.headers)
    assert listed.status_code == 200, listed.text
    teacher = next(row for row in listed.json() if row["teacher_id"] == str(hr_world.teacher_a.teacher_id))

    headcount = await api.get(f"{OPS}/reports/headcount", headers=admin.headers)
    assert headcount.status_code == 200, headcount.text
    assert headcount.json()["row_count"] >= 1

    csv_file = await api.get(f"{OPS}/reports/statutory", params={"format": "csv"}, headers=admin.headers)
    assert csv_file.status_code == 200, csv_file.text
    assert csv_file.headers["content-type"].startswith("text/csv")
    assert b"pan" in csv_file.content

    xlsx = await api.get(f"{OPS}/reports/department", params={"format": "xlsx"}, headers=admin.headers)
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"

    pdf = await api.get(
        f"{OPS}/reports/attendance",
        params={"format": "pdf", "date_from": "2026-04-01", "date_to": "2026-04-30"},
        headers=admin.headers,
    )
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    leave = await api.get(f"{OPS}/reports/leave", params={"format": "json"}, headers=admin.headers)
    assert leave.status_code == 200
    attrition = await api.get(f"{OPS}/reports/attrition", headers=admin.headers)
    assert attrition.status_code == 200

    student = await api.get(f"{OPS}/probations", headers=hr_world.student_a.headers)
    assert student.status_code == 403

    probation = await api.post(
        f"{OPS}/probations",
        json={"employee_id": teacher["id"], "start_date": "2026-04-01", "end_date": "2026-09-30"},
        headers=admin.headers,
    )
    assert probation.status_code == 201, probation.text
    confirmed = await api.post(
        f"{OPS}/probations/{probation.json()['id']}/decide",
        json={"action": "CONFIRM", "confirmed_on": "2026-10-01"},
        headers=admin.headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
    mine = await api.get(f"{OPS}/probations/me", headers=hr_world.teacher_a.headers)
    assert mine.status_code == 200
    assert any(row["id"] == probation.json()["id"] for row in mine.json())

    cycle = await api.post(
        f"{OPS}/appraisal-cycles",
        json={"name": "2026-27", "period_start": "2026-04-01", "period_end": "2027-03-31"},
        headers=admin.headers,
    )
    assert cycle.status_code == 201, cycle.text
    opened = await api.post(f"{OPS}/appraisal-cycles/{cycle.json()['id']}/open", headers=admin.headers)
    assert opened.status_code == 200, opened.text
    goal = await api.post(
        f"{OPS}/appraisal-goals",
        json={"cycle_id": cycle.json()["id"], "employee_id": teacher["id"], "title": "Lesson quality", "weight": "40"},
        headers=admin.headers,
    )
    assert goal.status_code == 201, goal.text
    own = await api.get(f"{OPS}/appraisals/me", headers=hr_world.teacher_a.headers)
    appraisal_id = next(row["id"] for row in own.json() if row["employee_id"] == teacher["id"])
    self_review = await api.post(
        f"{OPS}/appraisals/{appraisal_id}/self",
        json={"rating": "4.0", "comments": "Met the plan"},
        headers=hr_world.teacher_a.headers,
    )
    assert self_review.status_code == 200, self_review.text
    manager = await api.post(
        f"{OPS}/appraisals/{appraisal_id}/manager",
        json={"rating": "4.5", "comments": "Strong year"},
        headers=admin.headers,
    )
    assert manager.status_code == 200, manager.text
    final = await api.post(
        f"{OPS}/appraisals/{appraisal_id}/final",
        json={"rating": "4.5", "comments": "Confirmed"},
        headers=admin.headers,
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "FINAL"

    audited = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "hr.probation.start"))
    ).scalars().all()
    assert audited


async def test_hire_training_roster_assets_and_exit(api, hr_world):
    admin = hr_world.admin_a
    opening = await api.post(
        f"{OPS}/openings",
        json={"title": "Lab assistant", "publish": True, "openings": 1},
        headers=admin.headers,
    )
    assert opening.status_code == 201, opening.text
    application = await api.post(
        f"{OPS}/applications",
        json={"opening_id": opening.json()["id"], "applicant_name": "Meera Nair", "phone": "9000000099"},
        headers=admin.headers,
    )
    assert application.status_code == 201, application.text
    application_id = application.json()["id"]
    interview = await api.post(
        f"{OPS}/applications/{application_id}/interviews",
        json={"stage": "HR", "outcome": "PASSED"},
        headers=admin.headers,
    )
    assert interview.status_code == 201, interview.text
    offer = await api.post(
        f"{OPS}/applications/{application_id}/offer",
        json={"offered_on": "2026-10-01", "joining_date": "2026-10-15", "salary_note": "As discussed"},
        headers=admin.headers,
    )
    assert offer.status_code == 201, offer.text
    accepted = await api.post(f"{OPS}/offers/{offer.json()['id']}/accept", headers=admin.headers)
    assert accepted.status_code == 200, accepted.text
    hired = await api.post(
        f"{OPS}/applications/{application_id}/hire",
        json={"staff_type": "NON_TEACHING", "joining_date": "2026-10-15"},
        headers=admin.headers,
    )
    assert hired.status_code == 200, hired.text
    employee_id = hired.json()["employee_id"]
    assert hired.json()["workflow_id"]

    program = await api.post(f"{OPS}/training-programs", json={"title": "First aid", "valid_months": 12}, headers=admin.headers)
    assert program.status_code == 201, program.text
    record = await api.post(
        f"{OPS}/training-records",
        json={"program_id": program.json()["id"], "employee_id": employee_id, "completed_on": "2026-10-20", "certificate_no": "FA-1"},
        headers=admin.headers,
    )
    assert record.status_code == 201, record.text
    assert record.json()["status"] == "COMPLETED"

    shift = await api.post(
        f"{OPS}/shifts",
        json={"name": "Morning", "start_time": "08:00:00", "end_time": "14:00:00", "break_minutes": 30},
        headers=admin.headers,
    )
    assert shift.status_code == 201, shift.text
    roster = await api.post(
        f"{OPS}/roster",
        json={
            "employee_id": employee_id,
            "shift_id": shift.json()["id"],
            "duty_date": "2026-10-21",
            "duty": "DUTY",
            "timetable_period": "P1",
            "overtime_minutes": 90,
        },
        headers=admin.headers,
    )
    assert roster.status_code == 201, roster.text
    overtime = await api.get(f"{OPS}/roster/overtime", params={"date_from": "2026-10-01"}, headers=admin.headers)
    assert overtime.status_code == 200, overtime.text
    assert any(row["employee_id"] == employee_id and row["overtime_minutes"] == 90 for row in overtime.json())

    asset = await api.post(
        f"{OPS}/assets",
        json={"name": "Laptop", "asset_tag": f"LT-{uuid4().hex[:6]}", "cost": "25000.00"},
        headers=admin.headers,
    )
    assert asset.status_code == 201, asset.text
    issued = await api.post(
        f"{OPS}/assets/{asset.json()['id']}/issue",
        json={"employee_id": employee_id, "issued_on": "2026-10-15", "condition_out": "New"},
        headers=admin.headers,
    )
    assert issued.status_code == 201, issued.text
    returned = await api.post(
        f"{OPS}/asset-issues/{issued.json()['id']}/return",
        json={"returned_on": "2026-10-28", "condition_in": "Good"},
        headers=admin.headers,
    )
    assert returned.status_code == 200, returned.text

    leaver = await _employee(api, admin)
    kept = await api.post(
        f"{OPS}/assets",
        json={"name": "ID card printer", "asset_tag": f"PR-{uuid4().hex[:6]}", "cost": "500.00"},
        headers=admin.headers,
    )
    await api.post(
        f"{OPS}/assets/{kept.json()['id']}/issue",
        json={"employee_id": leaver["id"], "issued_on": "2026-04-02"},
        headers=admin.headers,
    )
    draft = await api.post(
        f"{OPS}/exits",
        json={
            "employee_id": leaver["id"],
            "exit_date": "2026-10-31",
            "exit_reason": "RESIGNATION",
            "lines": [{"kind": "DUE", "label": "Leave encashment", "amount": "12000.00"}],
        },
        headers=admin.headers,
    )
    assert draft.status_code == 201, draft.text
    body = draft.json()
    assert body["dues_total"] == "12000.00"
    assert body["recoveries_total"] == "500.00"
    assert body["payable"] == "11500.00"
    posted = await api.post(f"{OPS}/exits/{body['id']}/post", headers=admin.headers)
    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "POSTED"
    assert posted.json()["payroll_run_id"]
    assert posted.json()["payslip_id"]
