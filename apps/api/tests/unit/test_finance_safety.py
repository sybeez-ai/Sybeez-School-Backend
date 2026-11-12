"""Money-safety decisions and finance contract paths. No database."""

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.core.exceptions import ForbiddenError
from src.main import app
from src.modules.fees.crediting import assert_distinct_checker, classify_razorpay_event
from src.modules.fees.gateways.razorpay import RazorpayGateway
from src.modules.fees.numbering import format_series_number, indian_financial_year
from src.modules.finance.permissions import ACTIONS
from src.shared.enums import UserRole

client = TestClient(app)


def test_financial_year_boundaries():
    assert indian_financial_year(date(2026, 3, 31)) == (2025, "2025-26")
    assert indian_financial_year(date(2026, 4, 1)) == (2026, "2026-27")
    assert indian_financial_year(date(2026, 12, 31)) == (2026, "2026-27")
    assert format_series_number("REC", "2026-27", 1) == "REC-2026-27-000001"


def test_razorpay_event_credit_rules():
    captured = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"status": "captured", "order_id": "order_1", "id": "pay_1"}}},
    }
    failed = {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {"status": "failed", "order_id": "order_1", "id": "pay_1"}}},
    }
    authorized = {
        "event": "payment.authorized",
        "payload": {"payment": {"entity": {"status": "authorized", "order_id": "order_1"}}},
    }
    paid = {
        "event": "order.paid",
        "payload": {
            "payment": {"entity": {"status": "captured", "order_id": "order_1"}},
            "order": {"entity": {"status": "paid", "id": "order_1"}},
        },
    }
    assert classify_razorpay_event(captured)[0] is True
    assert classify_razorpay_event(failed)[0] is False
    assert classify_razorpay_event(authorized)[0] is False
    assert classify_razorpay_event(paid)[0] is True


@pytest.mark.asyncio
async def test_razorpay_webhook_signature_gates_credit(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test")
    gateway = RazorpayGateway()
    body = (
        b'{"event":"payment.failed","payload":{"payment":{"entity":'
        b'{"id":"pay_1","order_id":"order_1","status":"failed","amount":10000,"currency":"INR"}}}}'
    )
    import hashlib
    import hmac
    import json

    good = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    failed = await gateway.handle_webhook(
        payload=json.loads(body),
        headers={"x-razorpay-signature": good},
        raw_body=body,
    )
    assert failed.success is True
    assert failed.credit is False
    assert failed.order_id == "order_1"

    bad = await gateway.handle_webhook(
        payload=json.loads(body),
        headers={"x-razorpay-signature": "not-the-signature"},
        raw_body=body,
    )
    assert bad.success is False
    assert bad.credit is False


def test_maker_cannot_approve_own_request():
    user = uuid4()
    with pytest.raises(ForbiddenError):
        assert_distinct_checker(user, user)
    assert_distinct_checker(uuid4(), uuid4())


def test_finance_role_matrix():
    assert UserRole.AUDITOR.value not in ACTIONS["collect"]
    assert UserRole.AUDITOR.value not in ACTIONS["approve"]
    assert UserRole.CASHIER.value in ACTIONS["collect"]
    assert UserRole.CASHIER.value not in ACTIONS["approve"]
    assert UserRole.ACCOUNTANT.value in ACTIONS["approve"]
    assert UserRole.ACCOUNTANT.value in ACTIONS["collect"]


def spec_role_enum(document: dict) -> list[str]:
    schema = document["components"]["schemas"]["UserRole"]
    return list(schema["enum"])


def test_token_role_enum_includes_finance_roles():
    shared = Path(__file__).resolve().parents[4] / "packages" / "shared" / "enums" / "__init__.py"
    text = shared.read_text()
    for role in ("ACCOUNTANT", "CASHIER", "AUDITOR"):
        assert f'"{role}"' in text
    document = client.get("/openapi.json").json()
    assert {"ACCOUNTANT", "CASHIER", "AUDITOR"} <= set(spec_role_enum(document))
    issued = document["components"]["schemas"]["TokenResponse"]["properties"]["role"]
    assert "UserRole" in str(issued)


def test_finance_openapi_paths():
    document = client.get("/openapi.json").json()
    paths = document["paths"]
    required = (
        "/api/v1/finance/branches",
        "/api/v1/finance/financial-years",
        "/api/v1/finance/staff-scopes",
        "/api/v1/finance/students/search",
        "/api/v1/finance/students/{student_id}/profile",
        "/api/v1/finance/students/{student_id}/dues",
        "/api/v1/finance/students/{student_id}/ledger",
        "/api/v1/finance/sibling-groups",
        "/api/v1/finance/concession-rules",
        "/api/v1/finance/concessions",
        "/api/v1/finance/collections",
        "/api/v1/finance/receipts/{receipt_id}",
        "/api/v1/finance/receipts/{receipt_id}/pdf",
        "/api/v1/finance/receipts/{receipt_id}/reprint",
        "/api/v1/finance/receipts/{receipt_id}/cancellations",
        "/api/v1/finance/cheques",
        "/api/v1/finance/cheques/{tender_id}/deposit",
        "/api/v1/finance/cheques/{tender_id}/clear",
        "/api/v1/finance/cheques/{tender_id}/bounce",
        "/api/v1/finance/day-close",
        "/api/v1/finance/dashboard",
        "/api/v1/finance/reports/defaulters",
        "/api/v1/finance/reports/day-book",
        "/api/v1/finance/reports/head-wise",
        "/api/v1/finance/reports/mode-wise",
        "/api/v1/finance/reports/collector-wise",
        "/api/v1/finance/approvals",
        "/api/v1/finance/fine-waivers",
        "/api/v1/finance/payments/{payment_id}/refunds",
        "/api/v1/finance/refunds/{refund_id}/approve",
        "/api/v1/finance/audit",
        "/api/v1/finance/reminders/schedule",
        "/api/v1/finance/reports/concessions",
        "/api/v1/finance/reports/cancelled-receipts",
        "/api/v1/fees/collections",
        "/api/v1/fees/concessions",
        "/api/v1/fees/concessions/{concession_id}/approve",
        "/api/v1/fees/concessions/{concession_id}/reject",
        "/api/v1/fees/fine-waivers",
        "/api/v1/fees/cheques",
        "/api/v1/fees/cheques/{tender_id}/deposit",
        "/api/v1/fees/cheques/{tender_id}/clear",
        "/api/v1/fees/cheques/{tender_id}/bounce",
        "/api/v1/fees/receipts",
        "/api/v1/fees/receipts/{receipt_id}/cancel",
        "/api/v1/fees/receipts/{receipt_id}/duplicate",
        "/api/v1/fees/day-close",
        "/api/v1/fees/day-close/{day_close_id}/verify",
        "/api/v1/fees/reports/head-wise",
        "/api/v1/fees/reports/collector-wise",
        "/api/v1/fees/reports/collector",
        "/api/v1/fees/reports/concessions",
        "/api/v1/fees/reports/cancelled-receipts",
        "/api/v1/fees/reminders",
        "/api/v1/fees/reminder-log",
        "/api/v1/fees/audit",
        "/api/v1/fees/approvals",
        "/api/v1/fees/payment-links",
        "/api/v1/fees/online-payments/reconciliation",
        "/api/v1/fees/demands/generate",
        "/api/v1/finance/notifications",
        "/api/v1/finance/reports/branch-wise",
        "/api/v1/finance/reports/category-wise",
        "/api/v1/auth/memberships",
        "/api/v1/auth/switch-school",
        "/api/v1/schools/me/branches",
    )
    for path in required:
        assert path in paths, path
    collection = paths["/api/v1/fees/collections"]["post"]
    assert "CollectIn" in str(collection["requestBody"])
    assert "CollectionOut" in str(collection["responses"]["201"])
    finance_collection = paths["/api/v1/finance/collections"]["post"]
    assert "CollectIn" in str(finance_collection["requestBody"])
    assert {"ACCOUNTANT", "CASHIER", "AUDITOR"} <= set(spec_role_enum(document))
