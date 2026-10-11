"""The assistant chat stream uses the caller's payroll role and does not write until confirm."""

import json

PAYROLL = "/api/v1/payroll/assistant"


def _proposal_id(body: str) -> str:
    for block in body.split("\n\n"):
        if "event: proposal" not in block:
            continue
        data = next(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
        return json.loads(data)["proposal_id"]
    raise AssertionError(body)


async def test_chat_streams_a_read_and_a_navigation(api, hr_world):
    pending = await api.post(
        f"{PAYROLL}/chat",
        headers=hr_world.admin_a.headers,
        json={"messages": [{"role": "user", "content": "what is pending"}]},
    )
    assert pending.status_code == 200, pending.text
    assert "text/event-stream" in pending.headers["content-type"]
    assert "event: tool" in pending.text
    assert "list_pending" in pending.text
    assert "event: done" in pending.text

    opened = await api.post(
        f"{PAYROLL}/chat",
        headers=hr_world.admin_a.headers,
        json={"messages": [{"role": "user", "content": "open payroll settings"}]},
    )
    assert opened.status_code == 200, opened.text
    assert "event: navigate" in opened.text
    assert "/payroll/settings" in opened.text


async def test_chat_proposes_a_submit_and_confirm_requires_a_real_run(api, hr_world):
    run_id = "11111111-1111-1111-1111-111111111111"
    proposed = await api.post(
        f"{PAYROLL}/chat",
        headers=hr_world.admin_a.headers,
        json={"messages": [{"role": "user", "content": f"submit the pay run {run_id}"}]},
    )
    assert proposed.status_code == 200, proposed.text
    assert "event: proposal" in proposed.text
    assert "submit_run" in proposed.text
    proposal_id = _proposal_id(proposed.text)
    confirm = {
        "proposal_id": proposal_id,
        "idempotency_key": "confirm-missing-run",
    }
    first = await api.post(f"{PAYROLL}/confirm", headers=hr_world.admin_a.headers, json=confirm)
    second = await api.post(f"{PAYROLL}/confirm", headers=hr_world.admin_a.headers, json=confirm)
    assert first.status_code == 404, first.text
    assert second.status_code == 404, second.text

    anonymous = await api.post(f"{PAYROLL}/chat", json={"messages": [{"role": "user", "content": "what is pending"}]})
    assert anonymous.status_code == 401
