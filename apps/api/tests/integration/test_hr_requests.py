"""Phase 8: employee self-service HR requests (real FastAPI + PostgreSQL)."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

API = "/api/v1/hr"
REQ = f"{API}/requests"
ME = f"{API}/me/requests"


async def _employee_id(api, actor, admin):
    rows = (await api.get(f"{API}/employees", headers=admin.headers)).json()
    return next(r["id"] for r in rows if r.get("teacher_id") == str(actor.teacher_id))


async def test_my_profile(api, hr_world):
    w = hr_world
    r = await api.get(f"{API}/me/profile", headers=w.teacher_a.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["staff_type"] == "TEACHING" and body["full_name"] == "IT TEACHER"
    assert "notes" not in body and "temporary_password" not in body
    assert (
        await api.get(f"{API}/me/profile", headers=w.admin_a.headers)
    ).status_code == 403
    assert (
        await api.get(f"{API}/me/profile", headers=w.student_a.headers)
    ).status_code == 403
    assert (await api.get(f"{API}/me/profile")).status_code == 401


async def test_employee_submit_review_decide_history(api, hr_world):
    w = hr_world
    r = await api.post(
        ME,
        headers=w.teacher_a.headers,
        json={
            "request_type": "PROFILE_UPDATE",
            "subject": "  New phone number  ",
            "details": "Changed my SIM",
            "requested_changes": {"phone": " 98765 43210 ", "address": ""},
        },
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["subject"] == "New phone number"
    assert req["requested_changes"] == {"phone": "98765 43210"}
    assert req["current_values"] == {"phone": None}
    assert req["status"] == "PENDING" and req["source"] == "EMPLOYEE"
    assert [e["event_type"] for e in req["events"]] == ["SUBMITTED"]
    assert req["events"][0]["actor_is_employee"] is True

    listed = (
        await api.get(REQ, headers=w.admin_a.headers, params={"status": "OPEN"})
    ).json()
    assert req["id"] in {x["id"] for x in listed}
    assert all(x["events"] is None for x in listed)

    rv = await api.post(
        f"{REQ}/{req['id']}/decision",
        headers=w.admin_a.headers,
        json={"status": "IN_REVIEW"},
    )
    assert rv.status_code == 200 and rv.json()["status"] == "IN_REVIEW"
    assert rv.json()["decided_at"] is None

    c = await api.post(
        f"{REQ}/{req['id']}/comments",
        headers=w.admin_a.headers,
        json={"message": "Send proof please"},
    )
    assert c.status_code == 201
    c2 = await api.post(
        f"{ME}/{req['id']}/comments",
        headers=w.teacher_a.headers,
        json={"message": "Attached at office"},
    )
    assert c2.status_code == 201

    no_reason = await api.post(
        f"{REQ}/{req['id']}/decision",
        headers=w.admin_a.headers,
        json={"status": "REJECTED"},
    )
    assert no_reason.status_code == 422
    back = await api.post(
        f"{REQ}/{req['id']}/decision",
        headers=w.admin_a.headers,
        json={"status": "IN_REVIEW"},
    )
    assert back.status_code == 422

    ok = await api.post(
        f"{REQ}/{req['id']}/decision",
        headers=w.admin_a.headers,
        json={"status": "APPROVED", "note": "Updated in records"},
    )
    assert ok.status_code == 200
    done = ok.json()
    assert (
        done["status"] == "APPROVED" and done["decision_note"] == "Updated in records"
    )
    assert done["decided_by_name"] == "IT SCHOOL_ADMIN" and done["decided_at"]
    assert [e["event_type"] for e in done["events"]] == [
        "SUBMITTED",
        "STATUS_CHANGED",
        "COMMENT",
        "COMMENT",
        "STATUS_CHANGED",
    ]

    again = await api.post(
        f"{REQ}/{req['id']}/decision",
        headers=w.admin_a.headers,
        json={"status": "RESOLVED"},
    )
    assert again.status_code == 422
    late = await api.post(
        f"{ME}/{req['id']}/comments", headers=w.teacher_a.headers, json={"message": "x"}
    )
    assert late.status_code == 422
    assert (
        await api.post(f"{ME}/{req['id']}/cancel", headers=w.teacher_a.headers)
    ).status_code == 422

    mine = (await api.get(ME, headers=w.teacher_a.headers)).json()
    assert [m["status"] for m in mine] == ["APPROVED"]
    detail = (await api.get(f"{ME}/{req['id']}", headers=w.teacher_a.headers)).json()
    assert len(detail["events"]) == 5


async def test_cancel_and_validation(api, hr_world):
    w = hr_world
    bad = [
        {"request_type": "PAYROLL", "subject": "x"},
        {"request_type": "HR_QUERY", "subject": "  "},
        {
            "request_type": "HR_QUERY",
            "subject": "x",
            "requested_changes": {"phone": "1"},
        },
        {
            "request_type": "PROFILE_UPDATE",
            "subject": "x",
            "requested_changes": {"salary": "1"},
        },
        {"request_type": "PROFILE_UPDATE", "subject": "x"},
    ]
    for body in bad:
        assert (
            await api.post(ME, headers=w.teacher_a.headers, json=body)
        ).status_code == 422, body

    r = await api.post(
        ME,
        headers=w.teacher_a.headers,
        json={"request_type": "LETTER_REQUEST", "subject": "Experience letter"},
    )
    req = r.json()
    cancel = await api.post(f"{ME}/{req['id']}/cancel", headers=w.teacher_a.headers)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED" and cancel.json()["cancelled_at"]
    assert (
        await api.post(
            f"{REQ}/{req['id']}/decision",
            headers=w.admin_a.headers,
            json={"status": "APPROVED"},
        )
    ).status_code == 422


async def test_admin_files_on_behalf(api, hr_world):
    w = hr_world
    clerk = await api.post(
        f"{API}/employees",
        headers=w.admin_a.headers,
        json={
            "staff_type": "NON_TEACHING",
            "full_name": "Office Clerk",
            "employee_code": f"NT-{uuid4().hex[:5]}",
        },
    )
    clerk_id = clerk.json()["id"]
    r = await api.post(
        REQ,
        headers=w.admin_a.headers,
        json={
            "employee_id": clerk_id,
            "request_type": "DOCUMENT_REQUEST",
            "subject": "Salary certificate copy",
        },
    )
    assert r.status_code == 201, r.text
    assert (
        r.json()["source"] == "ADMIN"
        and r.json()["submitted_by_name"] == "IT SCHOOL_ADMIN"
    )
    assert r.json()["events"][0]["actor_is_employee"] is False

    other = await _employee_id(api, w.teacher_b, w.admin_b)
    cross = await api.post(
        REQ,
        headers=w.admin_a.headers,
        json={"employee_id": other, "request_type": "HR_QUERY", "subject": "x"},
    )
    assert cross.status_code == 404

    filtered = (
        await api.get(
            REQ, headers=w.admin_a.headers, params={"request_type": "document_request"}
        )
    ).json()
    assert [x["employee_name"] for x in filtered] == ["Office Clerk"]
    assert (
        await api.get(REQ, headers=w.admin_a.headers, params={"q": "certificate"})
    ).json()[0]["id"] == r.json()["id"]
    assert (
        await api.get(REQ, headers=w.admin_a.headers, params={"status": "nope"})
    ).status_code == 422


async def test_access_matrix(api, hr_world):
    w = hr_world
    req = (
        await api.post(
            ME,
            headers=w.teacher_a.headers,
            json={"request_type": "HR_QUERY", "subject": "Leave policy"},
        )
    ).json()
    rid = req["id"]

    assert (await api.get(f"{REQ}/{rid}", headers=w.admin_b.headers)).status_code == 404
    assert (
        await api.post(
            f"{REQ}/{rid}/decision",
            headers=w.admin_b.headers,
            json={"status": "RESOLVED"},
        )
    ).status_code == 404
    assert (
        await api.post(
            f"{REQ}/{rid}/comments", headers=w.admin_b.headers, json={"message": "x"}
        )
    ).status_code == 404
    assert rid not in {
        x["id"] for x in (await api.get(REQ, headers=w.admin_b.headers)).json()
    }

    for actor in (w.teacher_a2, w.teacher_b):
        assert (await api.get(f"{ME}/{rid}", headers=actor.headers)).status_code == 404
        assert (
            await api.post(f"{ME}/{rid}/cancel", headers=actor.headers)
        ).status_code == 404
        assert (
            await api.post(
                f"{ME}/{rid}/comments", headers=actor.headers, json={"message": "x"}
            )
        ).status_code == 404
        assert rid not in {
            x["id"] for x in (await api.get(ME, headers=actor.headers)).json()
        }

    for actor in (w.teacher_a, w.student_a):
        assert (await api.get(REQ, headers=actor.headers)).status_code == 403
        assert (
            await api.post(
                f"{REQ}/{rid}/decision",
                headers=actor.headers,
                json={"status": "RESOLVED"},
            )
        ).status_code == 403
    assert (await api.get(ME, headers=w.admin_a.headers)).status_code == 403
    assert (await api.get(ME, headers=w.student_a.headers)).status_code == 403
    assert (
        await api.post(
            ME,
            headers=w.student_a.headers,
            json={"request_type": "HR_QUERY", "subject": "x"},
        )
    ).status_code == 403
    assert (await api.get(REQ)).status_code == 401
    assert (await api.get(ME)).status_code == 401


async def test_db_constraints(api, hr_world, db_session):
    w = hr_world
    rid = (
        await api.post(
            ME,
            headers=w.teacher_a.headers,
            json={"request_type": "OTHER", "subject": "x"},
        )
    ).json()["id"]
    for stmt in (
        "UPDATE hr_requests SET status = 'APPROVED' WHERE id = :id",
        "UPDATE hr_requests SET status = 'WEIRD' WHERE id = :id",
        "UPDATE hr_requests SET source = 'BOT' WHERE id = :id",
        "UPDATE hr_requests SET school_id = :other WHERE id = :id",
        "UPDATE hr_request_events SET school_id = :other WHERE request_id = :id",
        "UPDATE hr_request_events SET event_type = 'DELETED' WHERE request_id = :id",
    ):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(stmt), {"id": rid, "other": w.admin_b.school_id}
                )
