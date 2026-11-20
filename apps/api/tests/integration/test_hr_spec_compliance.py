"""Regression tests for the HRM production-readiness specification remediation.

Covers: leave types / balances / working days / resubmission / no self-approval, the
approved-leave <-> attendance interaction, holidays and working days, check-in/out and
correction audit, employee manager / exit / rehire / history / pagination / photo /
auto codes, the STAFF self-service role, signed short-lived file links, document
verification and expiry, letter template versioning and blanks, notification idempotency.

Dates: 2026-09-07 is a Monday and 2026-09-13 a Sunday (Sundays are non-working by default).
"""

from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.core.security import create_access_token, encode_token
from src.db.models import (
    AuditLog,
    Employee,
    Notification,
    SchoolMembership,
    StaffLeaveRequest,
    User,
)
from src.modules.auth import cognito
from src.modules.hr_files import storage
from src.modules.hr_notifications.service import HrNotifier
from src.shared.enums import UserRole
from src.shared.utils import utcnow

API = "/api/v1/hr"
EMP = f"{API}/employees"
LEAVE = f"{API}/leave/requests"
ATT = f"{API}/attendance"
TYPES = f"{API}/leave-types"
SETTINGS = f"{API}/settings"
INBOX = "/api/v1/communication/notifications"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "hr-files")
    monkeypatch.setattr(cognito, "cognito_configured", lambda: False)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def _teacher_employee(api, admin, teacher):
    rows = (await api.get(EMP, headers=admin.headers)).json()
    return next(r for r in rows if r.get("teacher_id") == str(teacher.teacher_id))


async def _staff(api, admin, name="Ravi Clerk", **extra):
    return await _ok(
        await api.post(EMP, headers=admin.headers, json={"staff_type": "NON_TEACHING", "full_name": name, **extra}),
        201,
    )


async def _leave(api, actor, start, end, reason="Personal work", **extra):
    return await api.post(
        LEAVE, headers=actor.headers, json={"start_date": start, "end_date": end, "reason": reason, **extra}
    )


async def _decide(api, admin, leave_id, decision="APPROVED"):
    return await api.post(f"{LEAVE}/{leave_id}/decide", headers=admin.headers, json={"decision": decision})


async def _audits(db, action, resource_id=None):
    stmt = select(AuditLog).where(AuditLog.action == action)
    if resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == str(resource_id))
    return list((await db.execute(stmt)).scalars())


# ── Leave policy ────────────────────────────────────────────────────────────


async def test_leave_types_quota_and_balance_enforcement(api, hr_world):
    w = hr_world
    casual = await _ok(
        await api.post(TYPES, headers=w.admin_a.headers, json={"code": "casual", "name": "Casual", "annual_quota": 2}),
        201,
    )
    assert casual["code"] == "CASUAL"
    dup = await api.post(TYPES, headers=w.admin_a.headers, json={"code": "CASUAL", "name": "Again"})
    assert dup.status_code == 409

    untyped = await _leave(api, w.teacher_a, "2026-09-07", "2026-09-07")
    assert untyped.status_code == 422 and "leave type" in untyped.json()["error"]["message"]

    too_many = await _leave(api, w.teacher_a, "2026-09-07", "2026-09-09", leave_type_id=casual["id"])
    assert too_many.status_code == 422 and "balance" in too_many.json()["error"]["message"]

    ok = await _ok(await _leave(api, w.teacher_a, "2026-09-07", "2026-09-08", leave_type_id=casual["id"]), 201)
    assert ok["working_days"] == "2.0" or float(ok["working_days"]) == 2
    assert ok["leave_type_name"] == "Casual" and ok["leave_type_is_paid"] is True

    bal = await _ok(await api.get(f"{API}/leave/balances/me", headers=w.teacher_a.headers, params={"year": 2026}))
    row = next(b for b in bal["balances"] if b["leave_type_id"] == casual["id"])
    assert float(row["pending"]) == 2 and float(row["available"]) == 0

    held = await _leave(api, w.teacher_a, "2026-09-10", "2026-09-10", leave_type_id=casual["id"])
    assert held.status_code == 422, "pending requests hold the balance"

    other_school = await api.get(TYPES, headers=w.teacher_b.headers)
    assert all(t["id"] != casual["id"] for t in other_school.json())


async def test_working_days_skip_sundays_and_holidays(api, hr_world):
    w = hr_world
    await _ok(await api.post(f"{API}/holidays", headers=w.admin_a.headers, json={"holiday_date": "2026-09-08", "name": "Founders Day"}), 201)
    days = await _ok(
        await api.get(f"{API}/working-days", headers=w.admin_a.headers, params={"start_date": "2026-09-07", "end_date": "2026-09-13"})
    )
    assert days["working_days"] == 5

    week = await _ok(await _leave(api, w.teacher_a, "2026-09-07", "2026-09-13"), 201)
    assert float(week["working_days"]) == 5
    sunday = await _leave(api, w.teacher_a2, "2026-09-13", "2026-09-13")
    assert sunday.status_code == 422 and "non-working" in sunday.json()["error"]["message"]


async def test_admin_cannot_decide_own_leave(api, hr_world, db_session):
    w = hr_world
    own = StaffLeaveRequest(
        id=uuid4(), school_id=w.admin_a.school_id, user_id=w.admin_a.user_id,
        start_date=utcnow().date(), end_date=utcnow().date(), reason="Mine", status="PENDING",
    )
    db_session.add(own)
    await db_session.flush()
    r = await _decide(api, w.admin_a, own.id)
    assert r.status_code == 403


async def test_resubmission_rules(api, hr_world):
    w = hr_world
    first = await _ok(await _leave(api, w.teacher_a, "2026-11-02", "2026-11-03"), 201)
    pending_resubmit = await api.post(
        f"{LEAVE}/{first['id']}/resubmit", headers=w.teacher_a.headers,
        json={"start_date": "2026-11-04", "end_date": "2026-11-04", "reason": "Again"},
    )
    assert pending_resubmit.status_code == 422

    await _ok(await _decide(api, w.admin_a, first["id"], "REJECTED"))
    body = {"start_date": "2026-11-09", "end_date": "2026-11-10", "reason": "Rescheduled"}
    stranger = await api.post(f"{LEAVE}/{first['id']}/resubmit", headers=w.teacher_a2.headers, json=body)
    assert stranger.status_code == 403
    again = await _ok(await api.post(f"{LEAVE}/{first['id']}/resubmit", headers=w.teacher_a.headers, json=body), 201)
    assert again["resubmitted_from_id"] == first["id"] and again["status"] == "PENDING"
    twice = await api.post(f"{LEAVE}/{first['id']}/resubmit", headers=w.teacher_a.headers, json=body)
    assert twice.status_code == 409

    mine = await _ok(await api.get(f"{LEAVE}/me", headers=w.teacher_a.headers))
    original = next(x for x in mine if x["id"] == first["id"])
    assert original["resubmitted_as_id"] == again["id"]


# ── Attendance <-> approved leave (mandatory) ───────────────────────────────


async def test_approved_leave_owns_attendance_until_cancelled(api, hr_world, db_session):
    w = hr_world
    emp = await _teacher_employee(api, w.admin_a, w.teacher_a)
    earlier = await _ok(
        await api.post(ATT, headers=w.admin_a.headers, json={"employee_id": emp["id"], "attendance_date": "2026-09-08", "status": "PRESENT"}),
        201,
    )

    leave = await _ok(await _leave(api, w.teacher_a, "2026-09-07", "2026-09-09"), 201)
    await _ok(await _decide(api, w.admin_a, leave["id"]))

    marks = await _ok(
        await api.get(ATT, headers=w.admin_a.headers, params={"date_from": "2026-09-01", "date_to": "2026-09-30", "employee_id": emp["id"]})
    )
    assert sorted(m["attendance_date"] for m in marks) == ["2026-09-07", "2026-09-08", "2026-09-09"]
    assert all(m["status"] == "LEAVE" and m["leave_request_id"] == leave["id"] for m in marks)
    approval = await _audits(db_session, "hr.leave.approved", leave["id"])
    assert approval and approval[0].details["attendance_overwritten"] == [{"date": "2026-09-08", "status": "PRESENT"}]

    leave_mark = next(m for m in marks if m["attendance_date"] == "2026-09-08")
    assert leave_mark["id"] == earlier["id"]
    edit = await api.patch(f"{ATT}/{leave_mark['id']}", headers=w.admin_a.headers, json={"status": "PRESENT"})
    assert edit.status_code == 409
    remove = await api.delete(f"{ATT}/{leave_mark['id']}", headers=w.admin_a.headers)
    assert remove.status_code == 409
    bulk = await api.post(
        f"{ATT}/bulk", headers=w.admin_a.headers,
        json={"attendance_date": "2026-09-08", "entries": [{"employee_id": emp["id"], "status": "PRESENT"}]},
    )
    assert bulk.status_code == 409 and "approved leave" in bulk.json()["error"]["message"]

    sheet = await _ok(await api.get(f"{ATT}/daily", headers=w.admin_a.headers, params={"date": "2026-09-08"}))
    row = next(r for r in sheet["rows"] if r["employee_id"] == emp["id"])
    assert row["on_approved_leave"] is True and row["record"]["status"] == "LEAVE"

    revoked = await _ok(
        await api.post(f"{LEAVE}/{leave['id']}/revoke", headers=w.admin_a.headers, json={"reason": "Exam duty"})
    )
    assert revoked["status"] == "CANCELLED"
    after = await _ok(
        await api.get(ATT, headers=w.admin_a.headers, params={"date_from": "2026-09-01", "date_to": "2026-09-30", "employee_id": emp["id"]})
    )
    assert after == []
    assert await _audits(db_session, "hr.leave.revoked", leave["id"])
    mark = await api.post(ATT, headers=w.admin_a.headers, json={"employee_id": emp["id"], "attendance_date": "2026-09-08", "status": "PRESENT"})
    assert mark.status_code == 201
    titles = [n["title"] for n in (await api.get(INBOX, headers=w.teacher_a.headers, params={"scope": "hr"})).json()["items"]]
    assert "Your approved leave was cancelled by HR" in titles


async def test_new_marks_blocked_on_approved_leave_days(api, hr_world):
    w = hr_world
    emp = await _teacher_employee(api, w.admin_a, w.teacher_a2)
    leave = await _ok(await _leave(api, w.teacher_a2, "2026-09-14", "2026-09-14"), 201)
    await _ok(await _decide(api, w.admin_a, leave["id"]))
    rows = await _ok(
        await api.get(ATT, headers=w.admin_a.headers, params={"date_from": "2026-09-14", "date_to": "2026-09-14", "employee_id": emp["id"]})
    )
    assert len(rows) == 1 and rows[0]["status"] == "LEAVE"
    staff = await _staff(api, w.admin_a, "Not On Leave")
    blocked = await api.post(
        f"{ATT}/bulk", headers=w.admin_a.headers,
        json={"attendance_date": "2026-09-14", "entries": [{"employee_id": emp["id"], "status": "ABSENT"}, {"employee_id": staff["id"], "status": "PRESENT"}]},
    )
    assert blocked.status_code == 409, "the whole sheet is rejected, nothing is half-saved"
    kept = await api.post(
        f"{ATT}/bulk", headers=w.admin_a.headers,
        json={"attendance_date": "2026-09-14", "entries": [{"employee_id": emp["id"], "status": "LEAVE"}, {"employee_id": staff["id"], "status": "PRESENT"}]},
    )
    assert kept.status_code == 200, kept.text
    short = await api.post(f"{LEAVE}/{leave['id']}/revoke", headers=w.admin_a.headers, json={"reason": "x"})
    assert short.status_code == 422, "a reason is required"
    first = await api.post(f"{LEAVE}/{leave['id']}/revoke", headers=w.admin_a.headers, json={"reason": "Needed at school"})
    assert first.status_code == 200
    again = await api.post(f"{LEAVE}/{leave['id']}/revoke", headers=w.admin_a.headers, json={"reason": "Needed at school"})
    assert again.status_code == 422


async def test_attendance_rejected_on_holidays_and_weekly_off(api, hr_world):
    w = hr_world
    staff = await _staff(api, w.admin_a)
    sunday = await api.post(ATT, headers=w.admin_a.headers, json={"employee_id": staff["id"], "attendance_date": "2026-09-13", "status": "PRESENT"})
    assert sunday.status_code == 422 and "Sundays" in sunday.json()["error"]["message"]
    await _ok(await api.post(f"{API}/holidays", headers=w.admin_a.headers, json={"holiday_date": "2026-09-10", "name": "Festival"}), 201)
    holiday = await api.post(ATT, headers=w.admin_a.headers, json={"employee_id": staff["id"], "attendance_date": "2026-09-10", "status": "PRESENT"})
    assert holiday.status_code == 422 and "Festival" in holiday.json()["error"]["message"]
    sheet = await _ok(await api.get(f"{ATT}/daily", headers=w.admin_a.headers, params={"date": "2026-09-10"}))
    assert sheet["is_working_day"] is False and "Festival" in sheet["non_working_reason"]
    other = await api.post(ATT, headers=w.admin_b.headers, json={"employee_id": (await _teacher_employee(api, w.admin_b, w.teacher_b))["id"], "attendance_date": "2026-09-10", "status": "PRESENT"})
    assert other.status_code == 201, "holidays are per school"


async def test_check_in_out_late_flag_and_correction_audit(api, hr_world):
    w = hr_world
    await _ok(await api.patch(SETTINGS, headers=w.admin_a.headers, json={"work_start_time": "09:00", "late_grace_minutes": 10}))
    late_staff = await _staff(api, w.admin_a, "Late Larry")
    punctual = await _staff(api, w.admin_a, "Punctual Pam")
    mark = {"attendance_date": "2026-09-07", "status": "PRESENT", "check_out": "17:00"}
    late = await _ok(await api.post(ATT, headers=w.admin_a.headers, json={**mark, "employee_id": late_staff["id"], "check_in": "09:20"}), 201)
    ok = await _ok(await api.post(ATT, headers=w.admin_a.headers, json={**mark, "employee_id": punctual["id"], "check_in": "09:05"}), 201)
    assert late["is_late"] is True and ok["is_late"] is False

    bad_order = await api.post(ATT, headers=w.admin_a.headers, json={"employee_id": late_staff["id"], "attendance_date": "2026-09-08", "status": "PRESENT", "check_in": "10:00", "check_out": "09:00"})
    absent_times = await api.post(ATT, headers=w.admin_a.headers, json={"employee_id": late_staff["id"], "attendance_date": "2026-09-08", "status": "ABSENT", "check_in": "10:00"})
    assert bad_order.status_code == absent_times.status_code == 422

    fixed = await _ok(
        await api.patch(f"{ATT}/{late['id']}", headers=w.admin_a.headers, json={"status": "HALF_DAY", "reason": "Left at noon", "check_out": "12:00"})
    )
    assert fixed["status"] == "HALF_DAY" and fixed["check_out"] == "12:00:00"
    absent = await _ok(await api.patch(f"{ATT}/{late['id']}", headers=w.admin_a.headers, json={"status": "ABSENT"}))
    assert absent["check_in"] is None and absent["check_out"] is None
    trail = await _ok(await api.get(f"{ATT}/{late['id']}/history", headers=w.admin_a.headers))
    assert [t["action"] for t in trail] == ["hr.attendance.corrected", "hr.attendance.corrected"]
    first = next(t["details"] for t in trail if t["details"]["after"]["status"] == "HALF_DAY")
    assert first["before"]["status"] == "PRESENT" and first["reason"] == "Left at noon"
    assert first["before"]["check_out"] == "17:00:00" and first["after"]["check_out"] == "12:00:00"

    await _ok_delete(api, f"{ATT}/{late['id']}", w.admin_a, params={"reason": "Duplicate entry"})
    trail = await _ok(await api.get(f"{ATT}/{late['id']}/history", headers=w.admin_a.headers))
    deleted = [t for t in trail if t["action"] == "hr.attendance.deleted"]
    assert len(trail) == 3 and deleted[0]["details"]["reason"] == "Duplicate entry"
    assert (await api.get(f"{ATT}/{uuid4()}/history", headers=w.admin_a.headers)).status_code == 404
    assert (await api.get(f"{ATT}/{late['id']}/history", headers=w.admin_b.headers)).status_code == 404


async def test_day_sheet_corrections_keep_their_reason(api, hr_world):
    w = hr_world
    emp = await _staff(api, w.admin_a, "Sheet Sita")
    day = {"attendance_date": "2026-09-07"}
    first = await _ok(await api.post(f"{ATT}/bulk", headers=w.admin_a.headers, json={**day, "entries": [{"employee_id": emp["id"], "status": "ABSENT"}]}))
    rec_id = first["records"][0]["id"]
    await _ok(
        await api.post(
            f"{ATT}/bulk",
            headers=w.admin_a.headers,
            json={**day, "entries": [{"employee_id": emp["id"], "status": "PRESENT"}], "correction_reason": "  Was at the bank branch  "},
        )
    )
    trail = await _ok(await api.get(f"{ATT}/{rec_id}/history", headers=w.admin_a.headers))
    corrected = [t["details"] for t in trail if t["action"] == "hr.attendance.corrected"]
    assert len(corrected) == 1
    assert corrected[0]["reason"] == "Was at the bank branch" and corrected[0]["via"] == "day sheet"
    assert corrected[0]["before"]["status"] == "ABSENT" and corrected[0]["after"]["status"] == "PRESENT"
    too_long = await api.post(f"{ATT}/bulk", headers=w.admin_a.headers, json={**day, "entries": [], "correction_reason": "x" * 501})
    assert too_long.status_code == 422


async def _ok_delete(api, url, actor, params=None):
    r = await api.delete(url, headers=actor.headers, params=params)
    assert r.status_code == 204, r.text


# ── Employees ───────────────────────────────────────────────────────────────


async def test_reporting_manager_rules(api, hr_world):
    w = hr_world
    boss = await _staff(api, w.admin_a, "Boss Bina")
    report = await _staff(api, w.admin_a, "Report Raj", reporting_manager_id=boss["id"])
    assert report["reporting_manager_id"] == boss["id"] and report["reporting_manager_name"] == "Boss Bina"

    cycle = await api.patch(f"{EMP}/{boss['id']}", headers=w.admin_a.headers, json={"reporting_manager_id": report["id"]})
    assert cycle.status_code == 422 and "reports" in cycle.json()["error"]["message"]
    self_ = await api.patch(f"{EMP}/{boss['id']}", headers=w.admin_a.headers, json={"reporting_manager_id": boss["id"]})
    assert self_.status_code == 422
    foreign = await _teacher_employee(api, w.admin_b, w.teacher_b)
    cross = await api.patch(f"{EMP}/{report['id']}", headers=w.admin_a.headers, json={"reporting_manager_id": foreign["id"]})
    assert cross.status_code == 404

    team = await _ok(await api.get(EMP, headers=w.admin_a.headers, params={"reporting_manager_id": boss["id"]}))
    assert [e["full_name"] for e in team] == ["Report Raj"]


async def test_exit_and_rehire_keep_history(api, hr_world):
    w = hr_world
    dept = await _ok(await api.post(f"{API}/departments", headers=w.admin_a.headers, json={"name": "Accounts"}), 201)
    dept2 = await _ok(await api.post(f"{API}/departments", headers=w.admin_a.headers, json={"name": "Admin Office"}), 201)
    boss = await _staff(api, w.admin_a, "Head Hema")
    emp = await _staff(api, w.admin_a, "Mover Mani", department_id=dept["id"], joining_date="2025-06-01")
    await _ok(await api.patch(f"{EMP}/{emp['id']}", headers=w.admin_a.headers, json={"reporting_manager_id": boss["id"]}))
    moved = await _ok(
        await api.patch(f"{EMP}/{emp['id']}", headers=w.admin_a.headers, json={"department_id": dept2["id"], "effective_date": "2026-04-01", "change_note": "Restructure"})
    )
    assert moved["department_name"] == "Admin Office"

    early = await api.post(f"{EMP}/{emp['id']}/exit", headers=w.admin_a.headers, json={"exit_date": "2025-01-01", "exit_reason": "RESIGNATION"})
    assert early.status_code == 422
    gone = await _ok(
        await api.post(f"{EMP}/{emp['id']}/exit", headers=w.admin_a.headers, json={"exit_date": "2026-09-30", "exit_reason": "RESIGNATION", "notes": "Moving city"})
    )
    assert gone["status"] == "LEFT" and gone["is_active"] is False and gone["exit_reason"] == "RESIGNATION"
    assert (await api.post(f"{EMP}/{emp['id']}/exit", headers=w.admin_a.headers, json={"exit_date": "2026-09-30", "exit_reason": "OTHER"})).status_code == 409
    assert (await api.post(f"{EMP}/{emp['id']}/activate", headers=w.admin_a.headers)).status_code == 422
    left = await _ok(await api.get(EMP, headers=w.admin_a.headers, params={"status": "LEFT"}))
    assert [e["id"] for e in left] == [emp["id"]]

    boss_exit = await _ok(await api.post(f"{EMP}/{boss['id']}/exit", headers=w.admin_a.headers, json={"exit_date": "2026-09-30", "exit_reason": "RETIREMENT"}))
    assert boss_exit["status"] == "LEFT"

    back = await _ok(await api.post(f"{EMP}/{emp['id']}/rehire", headers=w.admin_a.headers, json={"rejoin_date": "2026-10-05"}))
    assert back["status"] == "ACTIVE" and back["exit_date"] is None and back["reporting_manager_id"] is None

    history = await _ok(await api.get(f"{EMP}/{emp['id']}/history", headers=w.admin_a.headers))
    events = [(h["event_type"], h["effective_date"]) for h in history]
    assert ("JOINED", "2025-06-01") in events
    assert ("DEPARTMENT_CHANGED", "2026-04-01") in events
    assert ("EXITED", "2026-09-30") in events and ("REHIRED", "2026-10-05") in events
    assert ("MANAGER_CHANGED", "2026-09-30") in events, "reports lose a manager who leaves"
    dept_change = next(h for h in history if h["event_type"] == "DEPARTMENT_CHANGED")
    assert dept_change["from_value"] == "Accounts" and dept_change["to_value"] == "Admin Office" and dept_change["note"] == "Restructure"
    assert (await api.get(f"{EMP}/{emp['id']}/history", headers=w.admin_b.headers)).status_code == 404


async def test_job_changes_cannot_take_effect_in_the_future(api, hr_world):
    w = hr_world
    dept = await _ok(await api.post(f"{API}/departments", headers=w.admin_a.headers, json={"name": "Stores"}), 201)
    emp = await _staff(api, w.admin_a, "Future Farah")
    future = (date.today() + timedelta(days=30)).isoformat()
    r = await api.patch(f"{EMP}/{emp['id']}", headers=w.admin_a.headers, json={"department_id": dept["id"], "effective_date": future})
    assert r.status_code == 422 and "future" in r.json()["error"]["message"]
    unchanged = await _ok(await api.get(f"{EMP}/{emp['id']}", headers=w.admin_a.headers))
    assert unchanged["department_id"] is None
    history = await _ok(await api.get(f"{EMP}/{emp['id']}/history", headers=w.admin_a.headers))
    assert not any(h["event_type"] == "DEPARTMENT_CHANGED" for h in history)
    today = await _ok(
        await api.patch(f"{EMP}/{emp['id']}", headers=w.admin_a.headers, json={"department_id": dept["id"], "effective_date": date.today().isoformat()})
    )
    assert today["department_id"] == dept["id"]


async def test_teacher_exit_disables_portal_access(api, hr_world):
    w = hr_world
    emp = await _teacher_employee(api, w.admin_a, w.teacher_a2)
    assert (await api.get(f"{LEAVE}/me", headers=w.teacher_a2.headers)).status_code == 200
    gone = await _ok(await api.post(f"{EMP}/{emp['id']}/exit", headers=w.admin_a.headers, json={"exit_date": "2026-09-30", "exit_reason": "TERMINATION"}))
    assert gone["status"] == "LEFT"
    assert (await api.get(f"{LEAVE}/me", headers=w.teacher_a2.headers)).status_code in (401, 403)
    back = await _ok(await api.post(f"{EMP}/{emp['id']}/rehire", headers=w.admin_a.headers, json={"rejoin_date": "2026-10-01"}))
    assert back["status"] == "ACTIVE" and back["is_active"] is True


async def test_pagination_sorting_and_total(api, hr_world):
    w = hr_world
    for name in ("Zara Zed", "Amit Able", "Mona Mid"):
        await _staff(api, w.admin_a, name)
    r = await api.get(EMP, headers=w.admin_a.headers, params={"staff_type": "NON_TEACHING", "limit": 2, "sort": "name"})
    assert r.status_code == 200 and r.headers["X-Total-Count"] == "3"
    assert [e["full_name"] for e in r.json()] == ["Amit Able", "Mona Mid"]
    page2 = await api.get(EMP, headers=w.admin_a.headers, params={"staff_type": "NON_TEACHING", "limit": 2, "offset": 2, "sort": "name"})
    assert [e["full_name"] for e in page2.json()] == ["Zara Zed"]
    desc = await api.get(EMP, headers=w.admin_a.headers, params={"staff_type": "NON_TEACHING", "sort": "name", "order": "desc"})
    assert [e["full_name"] for e in desc.json()] == ["Zara Zed", "Mona Mid", "Amit Able"]
    assert (await api.get(EMP, headers=w.admin_a.headers, params={"sort": "salary"})).status_code == 422


async def test_employee_code_prefix_generates_codes(api, hr_world):
    w = hr_world
    await _ok(await api.patch(SETTINGS, headers=w.admin_a.headers, json={"employee_code_prefix": "gv"}))
    a = await _staff(api, w.admin_a, "Code One")
    b = await _staff(api, w.admin_a, "Code Two")
    manual = await _staff(api, w.admin_a, "Code Manual", employee_code="X-1")
    assert (a["employee_code"], b["employee_code"], manual["employee_code"]) == ("GV0001", "GV0002", "X-1")


async def test_non_teaching_photo_upload(api, hr_world):
    w = hr_world
    emp = await _staff(api, w.admin_a, "Photo Priya")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    try:
        out = await _ok(await api.post(f"{EMP}/{emp['id']}/photo", headers=w.admin_a.headers, files={"file": ("p.png", png, "image/png")}))
        assert out["photo_url"] == f"/media/employee-photos/{emp['id']}.png"
        bad = await api.post(f"{EMP}/{emp['id']}/photo", headers=w.admin_a.headers, files={"file": ("p.txt", b"x", "text/plain")})
        assert bad.status_code == 422
    finally:
        media = Path(__file__).resolve().parents[2] / "media" / "employee-photos"
        for f in media.glob(f"{emp['id']}.*"):
            f.unlink()


# ── STAFF role (non-teaching self-service) ──────────────────────────────────


async def test_staff_login_and_self_service(api, hr_world, db_session):
    w = hr_world
    emp = await _staff(api, w.admin_a, "Staff Sunita")
    email = f"staff-{uuid4().hex[:8]}@example.com"
    granted = await _ok(await api.post(f"{EMP}/{emp['id']}/login", headers=w.admin_a.headers, json={"email": email, "password": "StaffPass123!"}))
    assert granted["has_login"] is True and granted["user_id"]
    again = await api.post(f"{EMP}/{emp['id']}/login", headers=w.admin_a.headers, json={"email": email})
    assert again.status_code == 409
    existing = User(id=uuid4(), email=f"existing-{uuid4().hex[:8]}@example.com", full_name="Existing", password_hash="x")
    db_session.add(existing)
    await db_session.flush()
    other = await _staff(api, w.admin_a, "Other Om")
    taken = await api.post(f"{EMP}/{other['id']}/login", headers=w.admin_a.headers, json={"email": existing.email.upper()})
    assert taken.status_code == 409, "an existing account is never re-used or re-passworded"
    await db_session.refresh(existing)
    assert existing.password_hash == "x"
    teaching = await _teacher_employee(api, w.admin_a, w.teacher_a)
    assert (await api.post(f"{EMP}/{teaching['id']}/login", headers=w.admin_a.headers, json={"email": "t@example.com"})).status_code == 422

    login = await api.post("/api/v1/auth/login", json={"email": email, "password": "StaffPass123!", "portal": "staff"})
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "STAFF"
    teacher_portal = await api.post("/api/v1/auth/login", json={"email": email, "password": "StaffPass123!", "portal": "teacher"})
    assert teacher_portal.status_code in (401, 403)
    assert login.json()["must_change_password"] is True
    changed = await api.post(
        "/api/v1/auth/change-password",
        json={"current_password": "StaffPass123!", "new_password": "Own-Staff-Pass-9!"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert changed.status_code == 200, changed.text
    login = await api.post("/api/v1/auth/login", json={"email": email, "password": "Own-Staff-Pass-9!", "portal": "staff"})
    staff = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await api.get(f"{API}/me/profile", headers=staff)).status_code == 200
    created = await api.post(LEAVE, headers=staff, json={"start_date": "2026-11-02", "end_date": "2026-11-02", "reason": "Clinic"})
    assert created.status_code == 201, created.text
    assert (await api.get(f"{API}/payroll/payslips/me", headers=staff)).json() == []
    assert (await api.get(f"{API}/me/letters", headers=staff)).status_code == 200
    assert (await api.get(f"{API}/me/files", headers=staff)).status_code == 200
    for admin_only in (EMP, f"{ATT}/daily?date=2026-09-07", LEAVE, f"{API}/dashboard", SETTINGS):
        assert (await api.get(admin_only, headers=staff)).status_code == 403, admin_only
    assert (await api.get("/api/v1/students", headers=staff)).status_code == 403

    await _ok(await _decide(api, w.admin_a, created.json()["id"]))
    note = (await api.get(INBOX, headers=staff, params={"scope": "hr"})).json()["items"][0]
    assert note["link_path"] == "/staff/leave"

    revoked = await _ok(await api.delete(f"{EMP}/{emp['id']}/login", headers=w.admin_a.headers))
    assert revoked["has_login"] is False
    assert (await api.get(f"{LEAVE}/me", headers=staff)).status_code in (401, 403)
    assert await _audits(db_session, "hr.employee.login_granted", emp["id"])
    assert await _audits(db_session, "hr.employee.login_revoked", emp["id"])


def _hr_routes():
    from src.main import app

    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/v1/hr/") and path != "/api/v1/hr/files/signed/{token}":
            for method in ops:
                yield method.upper(), path


async def test_staff_has_exactly_teacher_hr_access(api, hr_world, db_session):
    """STAFF reaches the same HRM self-service surface as a teacher and nothing more."""
    import re

    w = hr_world
    user = User(id=uuid4(), email=f"it-{uuid4().hex[:12]}@example.test", full_name="IT STAFF")
    db_session.add(user)
    await db_session.flush()
    db_session.add(SchoolMembership(user_id=user.id, school_id=w.admin_a.school_id, role=UserRole.STAFF.value))
    db_session.add(
        Employee(school_id=w.admin_a.school_id, staff_type="NON_TEACHING", full_name="IT STAFF", user_id=user.id, status="ACTIVE")
    )
    await db_session.flush()
    token = create_access_token(subject=user.id, role=UserRole.STAFF.value, school_id=w.admin_a.school_id)
    staff = type("Staff", (), {"headers": {"Authorization": f"Bearer {token}"}})()
    for method, path in _hr_routes():
        url = re.sub(r"\{[^}]+\}", lambda _: str(uuid4()), path)
        body = {} if method != "GET" else None
        t = await api.request(method, url, json=body, headers=w.teacher_a.headers)
        s = await api.request(method, url, json=body, headers=staff.headers)
        assert (t.status_code == 403) == (s.status_code == 403), (method, path, t.status_code, s.status_code)


# ── Files: signed links, verification, expiry ───────────────────────────────


async def _upload(api, admin, emp_id, **data):
    return await _ok(
        await api.post(
            f"{API}/files", headers=admin.headers,
            data={"employee_id": emp_id, "category": "CONTRACT", **data},
            files={"file": ("c.pdf", b"%PDF-1.4 signed", "application/pdf")},
        ),
        201,
    )


async def test_signed_download_links_are_short_lived_and_rechecked(api, hr_world):
    w = hr_world
    emp = await _teacher_employee(api, w.admin_a, w.teacher_a)
    doc = await _upload(api, w.admin_a, emp["id"], shared_with_employee="true")
    link = await _ok(await api.post(f"{API}/files/{doc['id']}/link", headers=w.admin_a.headers))
    assert link["storage"] == "local" and link["expires_in"] == storage.link_ttl()
    assert link["url"].startswith("/api/v1/hr/files/signed/")

    got = await api.get(link["url"])
    assert got.status_code == 200 and got.content == b"%PDF-1.4 signed"
    assert "no-store" in got.headers["cache-control"] and got.headers["x-content-type-options"] == "nosniff"
    assert (await api.get(link["url"] + "x")).status_code == 404

    expired = encode_token(
        {
            "type": "hr_file", "doc": doc["id"], "school_id": str(w.admin_a.school_id),
            "sub": str(w.admin_a.user_id), "adm": True, "exp": utcnow() - timedelta(seconds=5),
        }
    )
    assert (await api.get(f"/api/v1/hr/files/signed/{expired}")).status_code == 404

    access = create_access_token(subject=w.admin_a.user_id, role="SCHOOL_ADMIN", school_id=w.admin_a.school_id)
    assert (await api.get(f"/api/v1/hr/files/signed/{access}")).status_code == 404, "session tokens are not download tokens"

    mine = await _ok(await api.post(f"{API}/files/{doc['id']}/link", headers=w.teacher_a.headers))
    assert (await api.get(mine["url"])).status_code == 200
    assert (await api.post(f"{API}/files/{doc['id']}/link", headers=w.teacher_a2.headers)).status_code == 404
    await _ok(await api.post(f"{API}/files/{doc['id']}/archive", headers=w.admin_a.headers))
    assert (await api.get(mine["url"])).status_code == 404, "access is re-checked when the link is used"


async def test_verification_expiry_and_sensitive_audit(api, hr_world, db_session):
    w = hr_world
    emp = await _teacher_employee(api, w.admin_a, w.teacher_a)
    past = (utcnow().date() - timedelta(days=3)).isoformat()
    soon = (utcnow().date() + timedelta(days=5)).isoformat()
    expired = await _upload(api, w.admin_a, emp["id"], expiry_date=past)
    expiring = await _upload(api, w.admin_a, emp["id"], category="ID_PROOF", expiry_date=soon)
    assert expired["is_expired"] is True and expired["verification_status"] == "PENDING"
    assert expiring["expires_soon"] is True and expiring["is_expired"] is False
    only_expired = await _ok(await api.get(f"{API}/files", headers=w.admin_a.headers, params={"expiry": "EXPIRED"}))
    assert [d["id"] for d in only_expired] == [expired["id"]]

    no_note = await api.post(f"{API}/files/{expiring['id']}/verify", headers=w.admin_a.headers, json={"status": "REJECTED"})
    assert no_note.status_code == 422
    rejected = await _ok(await api.post(f"{API}/files/{expiring['id']}/verify", headers=w.admin_a.headers, json={"status": "REJECTED", "note": "Blurred scan"}))
    assert rejected["verification_status"] == "REJECTED" and rejected["verified_by_name"] and rejected["verified_at"]
    replaced = await _ok(
        await api.put(f"{API}/files/{expiring['id']}/file", headers=w.admin_a.headers, files={"file": ("n.pdf", b"%PDF-1.4 new", "application/pdf")})
    )
    assert replaced["verification_status"] == "PENDING"
    assert await _audits(db_session, "hr.document.verification.rejected", expiring["id"])

    medical = await _upload(api, w.admin_a, emp["id"], category="MEDICAL")
    assert medical["is_sensitive"] is True
    assert (await api.get(f"{API}/files/{medical['id']}/download", headers=w.admin_a.headers)).status_code == 200
    reads = await _audits(db_session, "hr.document.downloaded", medical["id"])
    assert len(reads) == 1 and reads[0].actor_id == w.admin_a.user_id
    await api.get(f"{API}/files/{expired['id']}/download", headers=w.admin_a.headers)
    assert not await _audits(db_session, "hr.document.downloaded", expired["id"]), "only sensitive reads are audited"

    checklist = await _ok(await api.get(f"{API}/files/checklist", headers=w.admin_a.headers))
    row = next(r for r in checklist["rows"] if r["employee_id"] == emp["id"])
    assert "CONTRACT" in row["expired"]


def test_s3_backend_presigns_private_downloads(monkeypatch):
    calls = {}

    class FakeS3:
        def put_object(self, **kw):
            calls["put"] = kw

        def generate_presigned_url(self, op, Params, ExpiresIn):
            calls["presign"] = (op, Params, ExpiresIn)
            return "https://bucket.example/obj?sig=1"

    backend = storage.S3Backend.__new__(storage.S3Backend)
    backend.bucket, backend.prefix, backend.client = "hr-bucket", "hr-files", FakeS3()
    backend.put("k1", b"data", "application/pdf")
    assert calls["put"]["ServerSideEncryption"] == "AES256" and calls["put"]["Key"].endswith("k1")
    url = backend.presigned_url("k1", "report card.pdf", "application/pdf", 60)
    op, params, ttl = calls["presign"]
    assert url.startswith("https://") and op == "get_object" and ttl == 60
    assert params["ResponseContentDisposition"].startswith("attachment;")


# ── Letters ─────────────────────────────────────────────────────────────────


async def test_letter_templates_are_versioned_and_blanks_block_issue(api, hr_world, db_session):
    w = hr_world
    emp = await _teacher_employee(api, w.admin_a, w.teacher_a)
    templates = await _ok(await api.get(f"{API}/letter-templates", headers=w.admin_a.headers))
    assert len(templates) == 12 and all(t["is_built_in"] for t in templates)

    body = {"title": "Offer - {employee_name}", "body": "Dear {employee_name}, welcome to {school_name}."}
    v1 = await _ok(await api.post(f"{API}/letter-templates/OFFER", headers=w.admin_a.headers, json=body), 201)
    same = await api.post(f"{API}/letter-templates/OFFER", headers=w.admin_a.headers, json=body)
    assert v1["version"] == 1 and same.status_code == 422
    v2 = await _ok(await api.post(f"{API}/letter-templates/OFFER", headers=w.admin_a.headers, json={**body, "body": body["body"] + " See you soon."}), 201)
    assert v2["version"] == 2
    versions = await _ok(await api.get(f"{API}/letter-templates/OFFER/versions", headers=w.admin_a.headers))
    assert [v["version"] for v in versions] == [2, 1]
    assert (await api.get(f"{API}/letter-templates", headers=w.admin_b.headers)).json()[0]["is_built_in"] is True

    letter = await _ok(await api.post(f"{API}/letters", headers=w.admin_a.headers, json={"employee_id": emp["id"], "letter_type": "OFFER"}), 201)
    assert letter["template_version"] == 2 and "See you soon." in letter["body"] and "{" not in letter["body"]
    issued = await _ok(await api.post(f"{API}/letters/{letter['id']}/issue", headers=w.admin_a.headers, json={}))
    assert issued["status"] == "ISSUED"
    frozen = await api.patch(f"{API}/letters/{letter['id']}", headers=w.admin_a.headers, json={"body": "changed"})
    assert frozen.status_code in (409, 422)
    assert await _audits(db_session, "hr.letter.issued", letter["id"])

    salary = await _ok(await api.post(f"{API}/letters", headers=w.admin_a.headers, json={"employee_id": emp["id"], "letter_type": "SALARY_CERTIFICATE"}), 201)
    assert "[amount]" in salary["body"]
    blocked = await api.post(f"{API}/letters/{salary['id']}/issue", headers=w.admin_a.headers, json={})
    assert blocked.status_code == 422 and "bracketed" in blocked.json()["error"]["message"]

    pdf = await api.get(f"{API}/letters/{letter['id']}/pdf", headers=w.admin_a.headers)
    assert pdf.status_code == 200 and pdf.headers["cache-control"] == "private, no-store"


# ── Notifications ───────────────────────────────────────────────────────────


async def test_hr_notifications_are_idempotent(hr_world, db_session):
    w = hr_world
    notifier = HrNotifier(db_session)
    entity = uuid4()
    for _ in range(3):
        await notifier.send(
            w.admin_a.school_id, [w.teacher_a.user_id], category="HR_REQUEST", title="Once", body="b",
            link="/teacher/my-hr", entity_type="hr_request", entity_id=entity, event="submitted", actor=None,
        )
    await notifier.send(
        w.admin_a.school_id, [w.teacher_a.user_id], category="HR_REQUEST", title="Twice", body="b",
        link="/teacher/my-hr", entity_type="hr_request", entity_id=entity, event="resolved", actor=None,
    )
    n = await db_session.scalar(select(func.count()).where(Notification.user_id == w.teacher_a.user_id))
    assert n == 2


# ── Settings ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"working_weekdays": []},
        {"working_weekdays": [7]},
        {"late_grace_minutes": 300},
        {"leave_min_notice_days": 120},
        {"document_expiry_warning_days": -1},
        {"employee_code_prefix": "G-V"},
        {"onboarding_checklist": ["  "]},
    ],
)
async def test_new_settings_validation(api, hr_world, body):
    r = await api.patch(SETTINGS, headers=hr_world.admin_a.headers, json=body)
    assert r.status_code == 422, (body, r.text)


async def test_settings_drive_notice_and_checklists(api, hr_world):
    w = hr_world
    await _ok(await api.patch(SETTINGS, headers=w.admin_a.headers, json={"leave_min_notice_days": 3, "onboarding_checklist": ["Collect ID", "Issue laptop"]}))
    tomorrow = (utcnow().date() + timedelta(days=1)).isoformat()
    short = await _leave(api, w.teacher_a, tomorrow, tomorrow)
    assert short.status_code == 422 and "3 days in advance" in short.json()["error"]["message"]
    tpl = await _ok(await api.get(f"{API}/workflows/template", headers=w.admin_a.headers, params={"workflow_type": "ONBOARDING"}))
    titles = [t["title"] if isinstance(t, dict) else t for t in (tpl["tasks"] if isinstance(tpl, dict) else tpl)]
    assert titles == ["Collect ID", "Issue laptop"]
    s = await _ok(await api.get(SETTINGS, headers=w.admin_a.headers))
    assert s["working_weekdays"] == [0, 1, 2, 3, 4, 5] and s["leave_balance_enforced"] is True
