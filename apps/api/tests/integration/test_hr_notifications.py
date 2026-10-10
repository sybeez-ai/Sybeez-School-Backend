"""Phase 9: HR events land in the shared notifications inbox for the right people only."""

import pytest
from sqlalchemy import select

from src.db.models import Notification
from src.modules.hr_files import storage
from src.modules.notifications.service import create_in_app_notification

API = "/api/v1/hr"
INBOX = "/api/v1/communication/notifications"


@pytest.fixture(autouse=True)
def _private_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "hr-files")


async def _inbox(api, actor, **params):
    r = await api.get(
        INBOX, headers=actor.headers, params={"scope": "hr", "page_size": 100, **params}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _titles(api, actor):
    return [n["title"] for n in (await _inbox(api, actor))["items"]]


async def _employee_id(api, actor, admin):
    rows = (await api.get(f"{API}/employees", headers=admin.headers)).json()
    return next(r["id"] for r in rows if r.get("teacher_id") == str(actor.teacher_id))


async def test_leave_notifications(api, hr_world):
    w = hr_world
    r = await api.post(
        f"{API}/leave/requests",
        json={
            "start_date": "2026-11-02",
            "end_date": "2026-11-03",
            "reason": "Family function",
        },
        headers=w.teacher_a.headers,
    )
    leave = r.json()
    admin_items = (await _inbox(api, w.admin_a))["items"]
    assert len(admin_items) == 1
    n = admin_items[0]
    assert n["category"] == "LEAVE_SUBMITTED" and n["link_path"] == "/hrm/leave"
    assert n["title"].startswith("Leave request from") and "(2 days)" in n["body"]
    assert n["entity_id"] == leave["id"] and n["is_read"] is False

    assert await _titles(api, w.teacher_a) == []
    assert await _titles(api, w.teacher_a2) == []
    assert await _titles(api, w.admin_b) == []

    await api.post(
        f"{API}/leave/requests/{leave['id']}/decide",
        json={"decision": "REJECTED", "decision_note": "Exams week"},
        headers=w.admin_a.headers,
    )
    mine = (await _inbox(api, w.teacher_a))["items"]
    assert [(m["category"], m["title"]) for m in mine] == [
        ("LEAVE_REJECTED", "Your leave request was rejected")
    ]
    assert mine[0]["link_path"] == "/teacher/leave" and "Exams week" in mine[0]["body"]
    assert len(await _titles(api, w.admin_a)) == 1

    r2 = await api.post(
        f"{API}/leave/requests",
        json={"start_date": "2026-12-01", "end_date": "2026-12-01", "reason": "Errand"},
        headers=w.teacher_a.headers,
    )
    await api.post(
        f"{API}/leave/requests/{r2.json()['id']}/cancel", headers=w.teacher_a.headers
    )
    assert (await _titles(api, w.admin_a))[0].startswith("Leave request withdrawn by")


async def test_request_letter_document_notifications(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)

    req = (
        await api.post(
            f"{API}/me/requests",
            headers=w.teacher_a.headers,
            json={"request_type": "LETTER_REQUEST", "subject": "Experience letter"},
        )
    ).json()
    assert (await _titles(api, w.admin_a)) == ["New letter request from IT TEACHER"]
    await api.post(
        f"{API}/requests/{req['id']}/comments",
        headers=w.admin_a.headers,
        json={"message": "Tomorrow"},
    )
    await api.post(
        f"{API}/me/requests/{req['id']}/comments",
        headers=w.teacher_a.headers,
        json={"message": "Thanks"},
    )
    await api.post(
        f"{API}/requests/{req['id']}/decision",
        headers=w.admin_a.headers,
        json={"status": "RESOLVED", "note": "Letter issued"},
    )
    assert await _titles(api, w.teacher_a) == [
        "Your request is resolved",
        "HR replied to your request",
    ]
    assert (await _titles(api, w.admin_a))[0] == "IT TEACHER commented on a request"

    letter = (
        await api.post(
            f"{API}/letters",
            headers=w.admin_a.headers,
            json={"employee_id": emp, "letter_type": "EXPERIENCE"},
        )
    ).json()
    assert len(await _titles(api, w.teacher_a)) == 2, "drafts are not announced"
    await api.post(
        f"{API}/letters/{letter['id']}/issue", headers=w.admin_a.headers, json={}
    )
    top = (await _inbox(api, w.teacher_a))["items"][0]
    assert top["category"] == "HR_LETTER" and top["title"].startswith("New HR letter")
    assert top["link_path"] == "/teacher/my-hr"

    pdf = {"file": ("c.pdf", b"%PDF-1.4 x", "application/pdf")}
    await api.post(
        f"{API}/files",
        headers=w.admin_a.headers,
        data={"employee_id": emp, "category": "MEDICAL"},
        files=pdf,
    )
    assert len(await _titles(api, w.teacher_a)) == 3, (
        "private documents are not announced"
    )
    shared = (
        await api.post(
            f"{API}/files",
            headers=w.admin_a.headers,
            data={
                "employee_id": emp,
                "category": "CONTRACT",
                "shared_with_employee": "true",
            },
            files=pdf,
        )
    ).json()
    await api.put(
        f"{API}/files/{shared['id']}/file", headers=w.admin_a.headers, files=pdf
    )
    assert (await _titles(api, w.teacher_a))[:2] == [
        "HR updated one of your documents",
        "HR shared a document with you",
    ]
    assert await _titles(api, w.teacher_a2) == []
    assert await _titles(api, w.teacher_b) == []


async def test_workflow_notifications(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    wf = (
        await api.post(
            f"{API}/workflows",
            headers=w.admin_a.headers,
            json={
                "employee_id": emp,
                "workflow_type": "ONBOARDING",
                "start_date": "2026-10-05",
                "tasks": [
                    {
                        "title": "Upload documents",
                        "assignee_user_id": str(w.teacher_a.user_id),
                    },
                    {"title": "ID"},
                ],
            },
        )
    ).json()
    assert await _titles(api, w.teacher_a) == [
        "Task assigned to you: Upload documents",
        "Your onboarding has started",
    ]
    assert await _titles(api, w.admin_a) == [], (
        "the actor is not notified about their own actions"
    )

    t0, t1 = (t["id"] for t in wf["tasks"])
    await api.post(
        f"{API}/workflows/{wf['id']}/tasks/{t0}/toggle",
        headers=w.admin_a.headers,
        json={"done": True},
    )
    await api.post(
        f"{API}/workflows/{wf['id']}/tasks/{t1}/toggle",
        headers=w.admin_a.headers,
        json={"done": True},
    )
    await api.post(f"{API}/workflows/{wf['id']}/complete", headers=w.admin_a.headers)
    titles = await _titles(api, w.teacher_a)
    assert titles[0] == "Your onboarding is complete" and len(titles) == 3
    assert await _titles(api, w.admin_b) == []


async def test_inbox_scope_read_state_and_isolation(api, hr_world, db_session):
    w = hr_world
    # A student-leave notification shares the LEAVE_* category but is not an HR event.
    await create_in_app_notification(
        db_session,
        school_id=w.admin_a.school_id,
        user_id=w.admin_a.user_id,
        title="Leave request submitted",
        body="x",
        category="LEAVE_SUBMITTED",
        entity_type="leave",
    )
    await api.post(
        f"{API}/me/requests",
        headers=w.teacher_a.headers,
        json={"request_type": "HR_QUERY", "subject": "PF"},
    )
    await api.post(
        f"{API}/me/requests",
        headers=w.teacher_a2.headers,
        json={"request_type": "HR_QUERY", "subject": "Gratuity"},
    )

    hr = await _inbox(api, w.admin_a)
    assert hr["total"] == 2 and hr["unread_count"] == 2
    everything = (await api.get(INBOX, headers=w.admin_a.headers)).json()
    assert everything["total"] == 3 and everything["unread_count"] == 3
    count = await api.get(
        f"{INBOX}/unread-count", headers=w.admin_a.headers, params={"scope": "hr"}
    )
    assert count.json() == {"count": 2}
    assert (
        await api.get(INBOX, headers=w.admin_a.headers, params={"scope": "payroll"})
    ).status_code == 422

    nid = hr["items"][0]["id"]
    assert (
        await api.post(f"{INBOX}/{nid}/read", headers=w.admin_b.headers)
    ).status_code == 404
    assert (
        await api.post(f"{INBOX}/{nid}/read", headers=w.teacher_a.headers)
    ).status_code == 404
    assert (
        await api.delete(f"{INBOX}/{nid}", headers=w.admin_b.headers)
    ).status_code == 404
    r = await api.post(f"{INBOX}/{nid}/read", headers=w.admin_a.headers)
    assert r.status_code == 200 and r.json()["is_read"] is True
    assert (await _inbox(api, w.admin_a))["unread_count"] == 1

    await api.post(
        f"{INBOX}/read-all", headers=w.admin_a.headers, params={"scope": "hr"}
    )
    assert (await _inbox(api, w.admin_a))["unread_count"] == 0
    assert (
        await api.get(f"{INBOX}/unread-count", headers=w.admin_a.headers)
    ).json() == {"count": 1}

    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.school_id == w.admin_a.school_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {r.user_id for r in rows} == {w.admin_a.user_id}
    assert (await api.get(INBOX, params={"scope": "hr"})).status_code == 401
    assert (await _inbox(api, w.student_a))["total"] == 0
