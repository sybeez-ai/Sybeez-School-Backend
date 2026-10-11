"""Import preview writes nothing until confirm, and get_report returns a signed file."""

import json

from src.modules.payroll.assistant.sheets import tally_sample_xlsx

PAYROLL = "/api/v1/payroll"
ASSISTANT = "/api/v1/payroll/assistant"


def _events(body: str, event: str) -> list[dict]:
    found = []
    for block in body.split("\n\n"):
        if f"event: {event}" not in block:
            continue
        data = next(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
        found.append(json.loads(data))
    return found


async def test_tally_import_previews_then_confirms_once(api, hr_world):
    uploaded = await api.post(
        f"{ASSISTANT}/import",
        headers=hr_world.admin_a.headers,
        files={"file": ("tally-pay-sheet.xlsx", tally_sample_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"financial_year": "2026-27", "effective_from": "2026-10-01"},
    )
    assert uploaded.status_code == 200, uploaded.text
    preview = uploaded.json()
    assert preview["counts"] == {"accepted": 1, "rejected": 5}
    assert preview["accepted"][0]["action"] == "create"
    assert preview["accepted"][0]["components"]["HRA"] == "12000.00"
    assert "ABCDE1234F" not in uploaded.text
    assert "123456789012" not in uploaded.text
    reasons = {item["reason"] for item in preview["rejected"]}
    assert {"missing PAN", "invalid IFSC", "duplicate code", "unknown component: Bonus", "unreadable cell: Basic Salary"} <= reasons

    before = await api.post(
        f"{ASSISTANT}/chat",
        headers=hr_world.admin_a.headers,
        json={"messages": [{"role": "user", "content": "find E001"}]},
    )
    assert before.status_code == 200, before.text
    assert _events(before.text, "tool_result")[0]["result"]["employees"] == []

    anonymous = await api.post(
        f"{ASSISTANT}/import",
        files={"file": ("tally-pay-sheet.xlsx", tally_sample_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert anonymous.status_code == 401
    teacher = await api.post(
        f"{ASSISTANT}/import",
        headers=hr_world.teacher_a.headers,
        files={"file": ("tally-pay-sheet.xlsx", tally_sample_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert teacher.status_code == 403

    confirm_body = {"idempotency_key": "import-tally-sheet"}
    first = await api.post(f"{ASSISTANT}/import/{preview['id']}/confirm", headers=hr_world.admin_a.headers, json=confirm_body)
    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False
    assert first.json()["result"]["created"] == 1
    assert first.json()["result"]["assignments"] == 1
    second = await api.post(f"{ASSISTANT}/import/{preview['id']}/confirm", headers=hr_world.admin_a.headers, json=confirm_body)
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True
    other_key = await api.post(
        f"{ASSISTANT}/import/{preview['id']}/confirm",
        headers=hr_world.admin_a.headers,
        json={"idempotency_key": "import-tally-other"},
    )
    assert other_key.status_code == 409
    other_school = await api.post(
        f"{ASSISTANT}/import/{preview['id']}/confirm",
        headers=hr_world.admin_b.headers,
        json=confirm_body,
    )
    assert other_school.status_code == 404

    found = await api.post(
        f"{ASSISTANT}/chat",
        headers=hr_world.admin_a.headers,
        json={"messages": [{"role": "user", "content": "find E001"}]},
    )
    people = _events(found.text, "tool_result")[0]["result"]["employees"]
    assert len(people) == 1
    employee_id = people[0]["id"]
    profile = await api.get(f"{PAYROLL}/employees/{employee_id}/profile", headers=hr_world.admin_a.headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["pan"] == "ABCDE1234F"
    assignments = await api.get(
        f"{PAYROLL}/salary-assignments",
        headers=hr_world.admin_a.headers,
        params={"employee_id": employee_id},
    )
    assert assignments.status_code == 200, assignments.text
    overrides = assignments.json()[0]["overrides"]
    assert overrides["BASIC"]["amount"] == "30000.00"
    assert "percent" not in overrides["HRA"]
    form16 = await api.get(
        f"{PAYROLL}/form16/{employee_id}",
        headers=hr_world.admin_a.headers,
        params={"financial_year": "2026-27"},
    )
    assert form16.status_code == 200, form16.text
    assert form16.json()["opening_gross"] == "180000.00"
    assert form16.json()["opening_tds"] == "12000.00"

    missing = await api.post(
        f"{ASSISTANT}/chat",
        headers=hr_world.admin_a.headers,
        json={"messages": [{"role": "user", "content": "find E002"}]},
    )
    assert _events(missing.text, "tool_result")[0]["result"]["employees"] == []
    rejects = await api.get(f"{ASSISTANT}/import/{preview['id']}/rejects.csv", headers=hr_world.admin_a.headers)
    assert rejects.status_code == 200, rejects.text
    assert "text/csv" in rejects.headers["content-type"]
    assert "missing PAN" in rejects.text
    assert "invalid IFSC" in rejects.text
    assert "ABCDE1234F" not in rejects.text


async def test_assistant_report_download_respects_the_caller(api, hr_world):
    asked = await api.post(
        f"{ASSISTANT}/chat",
        headers=hr_world.admin_a.headers,
        json={"messages": [{"role": "user", "content": "download the audit log"}]},
    )
    assert asked.status_code == 200, asked.text
    result = _events(asked.text, "tool_result")[0]["result"]
    assert result["kind"] == "audit"
    assert result["expires_in"] == 600
    downloaded = await api.get(result["url"])
    assert downloaded.status_code == 200, downloaded.text
    assert "text/csv" in downloaded.headers["content-type"]
    assert "action" in downloaded.text

    denied = await api.post(
        f"{ASSISTANT}/chat",
        headers=hr_world.teacher_a.headers,
        json={"messages": [{"role": "user", "content": "download the audit log"}]},
    )
    assert denied.status_code == 200, denied.text
    assert "event: error" in denied.text
    assert "/downloads/" not in denied.text
