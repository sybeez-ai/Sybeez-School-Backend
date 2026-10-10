"""HRM Phase 4: staff attendance persisted in PostgreSQL."""

from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models import Employee, StaffAttendance

ATT = "/api/v1/hr/attendance"
EMP = "/api/v1/hr/employees"
DEPTS = "/api/v1/hr/departments"

D1 = date(2026, 9, 1)
D2 = date(2026, 9, 2)


async def _staff(api, actor, name="Att Staff", **extra):
    r = await api.post(
        EMP,
        json={"staff_type": "NON_TEACHING", "full_name": name, **extra},
        headers=actor.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _teacher_employee(api, admin, teacher):
    rows = (
        await api.get(EMP, params={"staff_type": "TEACHING"}, headers=admin.headers)
    ).json()
    return next(e for e in rows if e["teacher_id"] == str(teacher.teacher_id))


async def _mark(api, actor, employee_id, day=D1, status="PRESENT", **extra):
    body = {
        "employee_id": employee_id,
        "attendance_date": day.isoformat(),
        "status": status,
        **extra,
    }
    return await api.post(ATT, json=body, headers=actor.headers)


async def _daily(api, actor, day=D1, **params):
    r = await api.get(
        f"{ATT}/daily",
        params={"date": day.isoformat(), **params},
        headers=actor.headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── Mark / update / delete ──────────────────────────────────


async def test_mark_update_and_delete(api, hr_world):
    a = hr_world.admin_a
    emp = await _staff(api, a, "Cook One")

    created = await _mark(api, a, emp["id"], status="HALF_DAY", notes="  left at noon ")
    assert created.status_code == 201, created.text
    rec = created.json()
    assert (
        rec["employee_name"],
        rec["status"],
        rec["notes"],
        rec["attendance_date"],
    ) == (
        "Cook One",
        "HALF_DAY",
        "left at noon",
        D1.isoformat(),
    )
    assert rec["marked_by"] == str(a.user_id)

    upd = await api.patch(
        f"{ATT}/{rec['id']}",
        json={"status": "PRESENT", "notes": None},
        headers=a.headers,
    )
    assert upd.status_code == 200, upd.text
    assert (upd.json()["status"], upd.json()["notes"]) == ("PRESENT", None)

    assert (
        await api.delete(f"{ATT}/{rec['id']}", headers=a.headers)
    ).status_code == 204
    assert (
        await api.patch(
            f"{ATT}/{rec['id']}", json={"status": "ABSENT"}, headers=a.headers
        )
    ).status_code == 404


async def test_duplicate_date_is_409(api, hr_world):
    a = hr_world.admin_a
    emp = await _staff(api, a, "Dup Mark")
    assert (await _mark(api, a, emp["id"])).status_code == 201

    dup = await _mark(api, a, emp["id"], status="ABSENT")
    other_day = await _mark(api, a, emp["id"], day=D2)

    assert dup.status_code == 409
    assert "already marked" in dup.json()["error"]["message"]
    assert other_day.status_code == 201


@pytest.mark.parametrize(
    "patch",
    [
        {"status": "LATE"},
        {"status": "present"},
        {"notes": "x" * 501},
        {"attendance_date": "2026-02-30"},
    ],
)
async def test_mark_validation(api, hr_world, patch):
    emp = await _staff(api, hr_world.admin_a, "Valid Staff")
    body = {
        "employee_id": emp["id"],
        "attendance_date": D1.isoformat(),
        "status": "PRESENT",
        **patch,
    }
    r = await api.post(ATT, json=body, headers=hr_world.admin_a.headers)
    assert r.status_code == 422, r.text


async def test_future_date_rejected(api, hr_world):
    emp = await _staff(api, hr_world.admin_a, "Future Staff")
    r = await _mark(
        api, hr_world.admin_a, emp["id"], day=date.today() + timedelta(days=5)
    )
    assert r.status_code == 422
    assert "future" in r.json()["error"]["message"]


async def test_inactive_employee_cannot_be_marked_but_existing_mark_is_kept(
    api, hr_world
):
    a = hr_world.admin_a
    emp = await _staff(api, a, "Leaving Staff")
    assert (await _mark(api, a, emp["id"], day=D1)).status_code == 201
    await api.post(f"{EMP}/{emp['id']}/deactivate", headers=a.headers)

    blocked = await _mark(api, a, emp["id"], day=D2)
    sheet_d1 = await _daily(api, a, D1)
    sheet_d2 = await _daily(api, a, D2)

    assert blocked.status_code == 422
    assert "inactive" in blocked.json()["error"]["message"]
    assert any(
        r["employee_id"] == emp["id"] and not r["employee_active"]
        for r in sheet_d1["rows"]
    )
    assert all(r["employee_id"] != emp["id"] for r in sheet_d2["rows"])


async def test_teacher_employee_can_be_marked(api, hr_world):
    a = hr_world.admin_a
    t = await _teacher_employee(api, a, hr_world.teacher_a)
    r = await _mark(api, a, t["id"], status="LEAVE")
    assert r.status_code == 201, r.text
    assert r.json()["employee_name"] == t["full_name"]


# ── Bulk ────────────────────────────────────────────────────


async def test_bulk_creates_then_overwrites(api, hr_world):
    a = hr_world.admin_a
    e1 = await _staff(api, a, "Bulk One")
    e2 = await _staff(api, a, "Bulk Two")
    entries = [
        {"employee_id": e1["id"], "status": "PRESENT"},
        {"employee_id": e2["id"], "status": "ABSENT"},
    ]

    first = await api.post(
        f"{ATT}/bulk",
        json={"attendance_date": D1.isoformat(), "entries": entries},
        headers=a.headers,
    )
    assert first.status_code == 200, first.text
    assert (first.json()["created"], first.json()["updated"]) == (2, 0)

    entries[1] = {"employee_id": e2["id"], "status": "HALF_DAY", "notes": "late"}
    second = await api.post(
        f"{ATT}/bulk",
        json={"attendance_date": D1.isoformat(), "entries": entries},
        headers=a.headers,
    )
    assert (second.json()["created"], second.json()["updated"]) == (0, 2)
    by_emp = {r["employee_id"]: r for r in second.json()["records"]}
    assert (by_emp[e2["id"]]["status"], by_emp[e2["id"]]["notes"]) == (
        "HALF_DAY",
        "late",
    )


async def test_bulk_rejects_duplicates_unknown_and_other_school_employees(
    api, hr_world
):
    a = hr_world.admin_a
    mine = await _staff(api, a, "Mine")
    theirs = await _staff(api, hr_world.admin_b, "Theirs")
    day = D1.isoformat()

    dup = await api.post(
        f"{ATT}/bulk",
        json={
            "attendance_date": day,
            "entries": [{"employee_id": mine["id"], "status": "PRESENT"}] * 2,
        },
        headers=a.headers,
    )
    cross = await api.post(
        f"{ATT}/bulk",
        json={
            "attendance_date": day,
            "entries": [
                {"employee_id": mine["id"], "status": "PRESENT"},
                {"employee_id": theirs["id"], "status": "PRESENT"},
            ],
        },
        headers=a.headers,
    )
    empty = await api.post(
        f"{ATT}/bulk", json={"attendance_date": day, "entries": []}, headers=a.headers
    )

    assert dup.status_code == 422
    assert cross.status_code == 404
    assert empty.status_code == 422
    assert (await _daily(api, a))["summary"]["present"] == 0


# ── Daily sheet / history / summary ─────────────────────────


async def test_daily_sheet_lists_active_staff_with_marks_and_summary(api, hr_world):
    a = hr_world.admin_a
    dept = (await api.post(DEPTS, json={"name": "Kitchen"}, headers=a.headers)).json()
    e1 = await _staff(api, a, "Daily One", department_id=dept["id"])
    e2 = await _staff(api, a, "Daily Two")
    await _mark(api, a, e1["id"], status="ABSENT")

    sheet = await _daily(api, a)
    rows = {r["employee_id"]: r for r in sheet["rows"]}
    assert rows[e1["id"]]["record"]["status"] == "ABSENT"
    assert rows[e1["id"]]["department_name"] == "Kitchen"
    assert rows[e2["id"]]["record"] is None
    assert sheet["summary"]["absent"] == 1
    assert sheet["summary"]["total"] == len(sheet["rows"])
    assert sheet["summary"]["unmarked"] == sheet["summary"]["total"] - 1

    only_absent = await _daily(api, a, status="ABSENT")
    only_unmarked = await _daily(api, a, status="UNMARKED")
    by_dept = await _daily(api, a, department_id=dept["id"])
    by_name = await _daily(api, a, q="daily two")
    assert [r["employee_id"] for r in only_absent["rows"]] == [e1["id"]]
    assert e1["id"] not in {r["employee_id"] for r in only_unmarked["rows"]}
    assert [r["employee_id"] for r in by_dept["rows"]] == [e1["id"]]
    assert [r["employee_id"] for r in by_name["rows"]] == [e2["id"]]


async def test_history_filters_and_range_validation(api, hr_world):
    a = hr_world.admin_a
    e1 = await _staff(api, a, "Hist One")
    e2 = await _staff(api, a, "Hist Two")
    await _mark(api, a, e1["id"], day=D1, status="PRESENT")
    await _mark(api, a, e1["id"], day=D2, status="ABSENT")
    await _mark(api, a, e2["id"], day=D2, status="PRESENT")
    await _mark(api, a, e2["id"], day=date(2026, 8, 31), status="PRESENT")

    def get(**params):
        return api.get(ATT, params=params, headers=a.headers)

    sept = (await get(month="2026-09")).json()
    assert [(r["employee_name"], r["attendance_date"]) for r in sept] == [
        ("Hist One", "2026-09-02"),
        ("Hist Two", "2026-09-02"),
        ("Hist One", "2026-09-01"),
    ]
    one = (
        await get(date_from="2026-08-01", date_to="2026-09-30", employee_id=e2["id"])
    ).json()
    assert [r["attendance_date"] for r in one] == ["2026-09-02", "2026-08-31"]
    absent = (await get(month="2026-09", status="ABSENT")).json()
    assert [r["employee_name"] for r in absent] == ["Hist One"]

    assert (await get(date_from="2026-09-10", date_to="2026-09-01")).status_code == 422
    assert (await get(date_from="2025-01-01", date_to="2026-09-01")).status_code == 422
    assert (await get()).status_code == 422
    assert (await get(month="2026-13")).status_code == 422


async def test_range_summary_counts_per_employee(api, hr_world):
    a = hr_world.admin_a
    emp = await _staff(api, a, "Sum Staff")
    for day, status in (
        (D1, "PRESENT"),
        (D2, "HALF_DAY"),
        (date(2026, 9, 3), "ABSENT"),
        (date(2026, 9, 4), "LEAVE"),
    ):
        assert (
            await _mark(api, a, emp["id"], day=day, status=status)
        ).status_code == 201

    r = await api.get(f"{ATT}/summary", params={"month": "2026-09"}, headers=a.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(e for e in body["employees"] if e["employee_id"] == emp["id"])
    assert (
        row["present"],
        row["half_day"],
        row["absent"],
        row["leave"],
        row["marked_days"],
    ) == (1, 1, 1, 1, 4)
    assert row["attendance_rate"] == 50.0
    assert (body["date_from"], body["date_to"]) == ("2026-09-01", "2026-09-30")
    assert body["totals"]["present"] >= 1


# ── Isolation / roles ───────────────────────────────────────


@pytest.mark.parametrize("actor", ["admin_b", "teacher_a", "student_a", "anonymous"])
async def test_other_school_and_non_admins_cannot_touch_attendance(
    api, hr_world, actor
):
    a = hr_world.admin_a
    emp = await _staff(api, a, "Isolated Staff")
    rec = (await _mark(api, a, emp["id"])).json()
    headers = {} if actor == "anonymous" else getattr(hr_world, actor).headers
    expected = {"admin_b": 404, "anonymous": 401}.get(actor, 403)

    assert (
        await api.patch(
            f"{ATT}/{rec['id']}", json={"status": "ABSENT"}, headers=headers
        )
    ).status_code == expected
    assert (
        await api.delete(f"{ATT}/{rec['id']}", headers=headers)
    ).status_code == expected
    mark = await api.post(
        ATT,
        json={
            "employee_id": emp["id"],
            "attendance_date": D2.isoformat(),
            "status": "ABSENT",
        },
        headers=headers,
    )
    assert mark.status_code == expected

    daily = await api.get(
        f"{ATT}/daily", params={"date": D1.isoformat()}, headers=headers
    )
    history = await api.get(ATT, params={"month": "2026-09"}, headers=headers)
    if actor == "admin_b":
        assert emp["id"] not in {r["employee_id"] for r in daily.json()["rows"]}
        assert history.json() == []
    else:
        assert daily.status_code == expected
        assert history.status_code == expected

    still = (await _daily(api, a))["rows"]
    assert (
        next(r for r in still if r["employee_id"] == emp["id"])["record"]["status"]
        == "PRESENT"
    )


# ── Database constraints ────────────────────────────────────


async def test_db_rejects_duplicate_day_and_cross_school_employee(db_session, hr_world):
    emp = Employee(
        school_id=hr_world.admin_a.school_id,
        staff_type="NON_TEACHING",
        full_name="DB Staff",
    )
    db_session.add(emp)
    await db_session.flush()
    db_session.add(
        StaffAttendance(
            school_id=emp.school_id,
            employee_id=emp.id,
            attendance_date=D1,
            status="PRESENT",
        )
    )
    await db_session.flush()

    for bad in (
        StaffAttendance(
            school_id=emp.school_id,
            employee_id=emp.id,
            attendance_date=D1,
            status="ABSENT",
        ),
        StaffAttendance(
            school_id=hr_world.admin_b.school_id,
            employee_id=emp.id,
            attendance_date=D2,
            status="PRESENT",
        ),
        StaffAttendance(
            school_id=emp.school_id,
            employee_id=emp.id,
            attendance_date=D2,
            status="LATE",
        ),
    ):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(bad)
                await db_session.flush()
