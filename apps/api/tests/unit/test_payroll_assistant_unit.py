"""Payroll assistant tools, masking, proposals, and the streaming chat contract."""

from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.payroll.assistant.catalog import route_for
from src.modules.payroll.assistant.mask import mask_account, mask_pan, mask_payload, redact_text
from src.modules.payroll.assistant.planner import plan_local, sse, summarize
from src.modules.payroll.assistant.service import _write_payload, confirm_decision

client = TestClient(app)


def test_openapi_includes_assistant_routes():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/payroll/assistant/chat" in paths
    assert "/api/v1/payroll/assistant/confirm" in paths
    assert "/api/v1/payroll/assistant/import" in paths
    assert "/api/v1/payroll/assistant/import/{import_id}/confirm" in paths
    assert "/api/v1/payroll/assistant/import/{import_id}/rejects.csv" in paths
    assert "/api/v1/payroll/assistant/downloads/{token}" in paths
    assert "text/event-stream" in paths["/api/v1/payroll/assistant/chat"]["post"]["responses"]["200"]["content"]


def test_masks_pan_and_bank_numbers():
    assert mask_pan("ABCDE1234F") == "******234F"
    assert mask_account("123456789012") == "********9012"
    masked = mask_payload({"pan": "ABCDE1234F", "account_number": "123456789012", "name": "Asha"})
    assert masked["pan"].endswith("234F") and "ABCDE" not in masked["pan"]
    assert "123456789012" not in masked["account_number"]
    assert "ABCDE1234F" not in redact_text("PAN ABCDE1234F account 123456789012")


def test_local_planner_reads_navigates_and_proposes():
    assert plan_local([{"role": "user", "content": "what is pending"}])[0].name == "list_pending"
    assert plan_local([{"role": "user", "content": "statutory dues"}])[0].name == "get_statutory_dues"
    assert plan_local([{"role": "user", "content": "open payroll settings"}])[0].name == "open_settings"
    run_id = "11111111-1111-1111-1111-111111111111"
    proposed = plan_local([{"role": "user", "content": f"submit the pay run {run_id}"}])[0]
    assert proposed.name == "submit_run"
    assert proposed.arguments["run_id"] == run_id
    payload, error = _write_payload("submit_run", proposed.arguments)
    assert error is None and payload["run_id"] == run_id
    _payload, missing = _write_payload("reject_run", {"run_id": run_id})
    assert missing
    assert route_for("open_run", {}) is None
    assert route_for("open_run", {"run_id": run_id})[0] == f"/payroll/runs/{run_id}"


def test_confirm_is_idempotent_and_a_reused_key_conflicts():
    assert confirm_decision(status="PROPOSED", stored_key=None, incoming_key="same-key", key_owner_is_other=False) == "execute"
    assert confirm_decision(status="EXECUTED", stored_key="same-key", incoming_key="same-key", key_owner_is_other=False) == "replay"
    assert confirm_decision(status="EXECUTED", stored_key="same-key", incoming_key="other-key", key_owner_is_other=False) == "conflict"
    assert confirm_decision(status="PROPOSED", stored_key=None, incoming_key="same-key", key_owner_is_other=True) == "conflict"


def test_summary_tells_the_user_to_confirm_a_write():
    text = summarize(
        [
            {
                "kind": "proposal",
                "action": "submit_run",
                "summary": "Prepare submit run",
                "proposal_id": str(uuid4()),
            }
        ]
    )
    assert "Confirm" in text
    frame = sse("proposal", {"proposal_id": "abc", "action": "submit_run"})
    assert frame.startswith("event: proposal\n")
    assert "submit_run" in frame
