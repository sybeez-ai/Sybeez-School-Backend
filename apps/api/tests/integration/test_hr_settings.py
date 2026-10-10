"""Phase 10: per-school HR business settings — defaults, validation, access, isolation, effects."""

import pytest
from sqlalchemy import select

from src.db.models import HrSettings, School
from src.shared.enums import UserRole
from tests.integration.conftest import _actor

API = "/api/v1/hr"
SETTINGS = f"{API}/settings"
INBOX = "/api/v1/communication/notifications"


async def _settings(api, actor):
    r = await api.get(SETTINGS, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _patch(api, actor, body, expect=200):
    r = await api.patch(SETTINGS, headers=actor.headers, json=body)
    assert r.status_code == expect, r.text
    return r.json()


async def _employee_id(api, actor, admin):
    rows = (await api.get(f"{API}/employees", headers=admin.headers)).json()
    return next(r["id"] for r in rows if r.get("teacher_id") == str(actor.teacher_id))


async def test_defaults_are_returned_without_writing(api, hr_world, db_session):
    w = hr_world
    s = await _settings(api, w.admin_a)
    assert s["is_default"] is True and s["updated_at"] is None
    assert s["letter_reference_prefix"] == "HR"
    assert s["required_document_categories"] == [
        "ID_PROOF",
        "CONTRACT",
        "QUALIFICATION",
        "BANK",
    ]
    assert s["leave_max_days"] == 366 and s["onboarding_target_days"] == 14
    assert all(s[k] for k in s if k.startswith("notify_"))
    assert s["school_timezone"]
    rows = (
        (
            await db_session.execute(
                select(HrSettings).where(HrSettings.school_id == w.admin_a.school_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_update_persists_with_timestamps_and_is_shared_by_admins(
    api, hr_world, db_session
):
    w = hr_world
    saved = await _patch(
        api,
        w.admin_a,
        {
            "letter_reference_prefix": " gvs ",
            "required_document_categories": ["CONTRACT", "BANK", "CONTRACT"],
            "notify_documents": False,
        },
    )
    assert saved["letter_reference_prefix"] == "GVS"
    assert saved["required_document_categories"] == ["CONTRACT", "BANK"]
    assert saved["notify_documents"] is False and saved["notify_leave"] is True
    assert saved["is_default"] is False and saved["updated_at"]
    assert saved["updated_by_name"] == "IT SCHOOL_ADMIN"
    first_stamp = saved["updated_at"]

    other_admin = await _actor(
        db_session, await _school_of(db_session, w.admin_a), UserRole.SCHOOL_ADMIN
    )
    seen = await _settings(api, other_admin)
    assert (
        seen["letter_reference_prefix"] == "GVS" and seen["notify_documents"] is False
    )
    again = await _patch(api, other_admin, {"leave_max_days": 30})
    assert again["leave_max_days"] == 30 and again["letter_reference_prefix"] == "GVS"
    assert again["updated_at"] >= first_stamp

    rows = (
        (
            await db_session.execute(
                select(HrSettings).where(HrSettings.school_id == w.admin_a.school_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].updated_by == other_admin.user_id

    b = await _settings(api, w.admin_b)
    assert b["is_default"] is True and b["letter_reference_prefix"] == "HR"


async def _school_of(db_session, actor):
    return (
        await db_session.execute(select(School).where(School.id == actor.school_id))
    ).scalar_one()


@pytest.mark.parametrize(
    "body",
    [
        {"letter_reference_prefix": "HR-X"},
        {"letter_reference_prefix": ""},
        {"letter_reference_prefix": "ABCDEFGHIJK"},
        {"required_document_categories": ["PASSPORT"]},
        {"leave_max_days": 0},
        {"leave_max_days": 400},
        {"onboarding_target_days": -1},
        {"onboarding_target_days": 366},
        {"notify_leave": None},
        {"school_timezone": "Asia/Kolkata"},
    ],
)
async def test_validation(api, hr_world, body):
    r = await api.patch(SETTINGS, headers=hr_world.admin_a.headers, json=body)
    assert r.status_code == 422, (body, r.text)
    assert (await _settings(api, hr_world.admin_a))["is_default"] is True


async def test_empty_required_documents_is_allowed(api, hr_world):
    s = await _patch(api, hr_world.admin_a, {"required_document_categories": []})
    assert s["required_document_categories"] == []
    r = await api.get(f"{API}/files/checklist", headers=hr_world.admin_a.headers)
    assert r.json()["required"] == []


async def test_access_matrix(api, hr_world):
    w = hr_world
    for actor in (w.teacher_a, w.student_a, w.teacher_b):
        assert (await api.get(SETTINGS, headers=actor.headers)).status_code == 403
        assert (
            await api.patch(SETTINGS, headers=actor.headers, json={"leave_max_days": 5})
        ).status_code == 403
    assert (await api.get(SETTINGS)).status_code == 401
    assert (await api.patch(SETTINGS, json={"leave_max_days": 5})).status_code == 401
    assert (await _settings(api, w.admin_a))["is_default"] is True


async def test_settings_drive_hr_behaviour(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    await _patch(
        api,
        w.admin_a,
        {
            "letter_reference_prefix": "GVS",
            "required_document_categories": ["CONTRACT"],
            "leave_max_days": 3,
            "onboarding_target_days": 10,
            "notify_letters": False,
        },
    )

    letter = (
        await api.post(
            f"{API}/letters",
            headers=w.admin_a.headers,
            json={
                "employee_id": emp,
                "letter_type": "EXPERIENCE",
                "issue_date": "2026-10-01",
            },
        )
    ).json()
    issued = (
        await api.post(
            f"{API}/letters/{letter['id']}/issue", headers=w.admin_a.headers, json={}
        )
    ).json()
    assert issued["reference_number"] == "GVS/2026/0001"
    inbox = (
        await api.get(INBOX, headers=w.teacher_a.headers, params={"scope": "hr"})
    ).json()
    assert inbox["total"] == 0, "letter notifications were switched off"

    checklist = (
        await api.get(f"{API}/files/checklist", headers=w.admin_a.headers)
    ).json()
    assert checklist["required"] == ["CONTRACT"]

    too_long = await api.post(
        f"{API}/leave/requests",
        headers=w.teacher_a.headers,
        json={"start_date": "2026-11-02", "end_date": "2026-11-05", "reason": "Trip"},
    )
    assert too_long.status_code == 422
    assert "cannot exceed 3 days" in too_long.json()["error"]["message"]
    ok = await api.post(
        f"{API}/leave/requests",
        headers=w.teacher_a.headers,
        json={"start_date": "2026-11-02", "end_date": "2026-11-04", "reason": "Trip"},
    )
    assert ok.status_code == 201, ok.text
    admin_inbox = (
        await api.get(INBOX, headers=w.admin_a.headers, params={"scope": "hr"})
    ).json()
    assert admin_inbox["total"] == 1, "leave notifications are still on"

    wf = (
        await api.post(
            f"{API}/workflows",
            headers=w.admin_a.headers,
            json={
                "employee_id": emp,
                "workflow_type": "ONBOARDING",
                "start_date": "2026-10-05",
            },
        )
    ).json()
    assert wf["expected_completion_date"] == "2026-10-15"

    # School B keeps the defaults: HR prefix and the 366-day ceiling.
    emp_b = await _employee_id(api, w.teacher_b, w.admin_b)
    letter_b = (
        await api.post(
            f"{API}/letters",
            headers=w.admin_b.headers,
            json={
                "employee_id": emp_b,
                "letter_type": "EXPERIENCE",
                "issue_date": "2026-10-01",
            },
        )
    ).json()
    issued_b = (
        await api.post(
            f"{API}/letters/{letter_b['id']}/issue", headers=w.admin_b.headers, json={}
        )
    ).json()
    assert issued_b["reference_number"] == "HR/2026/0001"
    long_b = await api.post(
        f"{API}/leave/requests",
        headers=w.teacher_b.headers,
        json={"start_date": "2026-11-02", "end_date": "2026-11-10", "reason": "Trip"},
    )
    assert long_b.status_code == 201, long_b.text
