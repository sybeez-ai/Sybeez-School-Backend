"""Razorpay Route: fees, transfers, webhooks, onboarding, and RLS contract."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from src.core.exceptions import AppError
from src.modules.fees.field_crypto import decrypt_account_number, encrypt_account_number, mask_account_number
from src.modules.fees.gateways.razorpay import RazorpayGateway
from src.modules.fees.gateways.razorpay_route import (
    RazorpayRouteClient,
    account_payload,
    settlement_patch,
    stakeholder_payload,
)
from src.modules.fees.route_events import describe_webhook
from src.modules.fees.route_money import (
    PlatformFeeError,
    plan_razorpay_transfers,
    split_platform_fee,
)
from src.modules.fees.route_reports import reconcile_payment
from src.modules.fees.route_schemas import RouteAccountIn, RouteSettlementIn, RouteStakeholderIn
from src.modules.fees.route_state import (
    RouteStateError,
    assert_can_add_stakeholder,
    assert_can_configure_product,
    assert_can_create_account,
    merge_transfer_status,
)

MIGRATION = Path(__file__).resolve().parents[2] / "src" / "db" / "migrations" / "versions" / "0055_razorpay_route.py"


def _settings(**kwargs):
    base = dict(
        gateway_provider="razorpay",
        route_status="not_started",
        linked_account_id=None,
        allow_platform_settlement=False,
        platform_fee_percent=Decimal("0"),
        platform_fee_fixed=None,
        settlement_on_hold=False,
        settlement_hold_until=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_platform_fee_rounding_edges():
    zero = split_platform_fee(Decimal("100.00"), Decimal("0"), None)
    assert (zero.fee_paise, zero.transfer_paise) == (0, 10000)

    exact = split_platform_fee(Decimal("100.00"), Decimal("10"), None)
    assert (exact.fee_paise, exact.transfer_paise, exact.fee_amount, exact.transfer_amount) == (
        1000,
        9000,
        Decimal("10.00"),
        Decimal("90.00"),
    )

    half_up = split_platform_fee(Decimal("1.00"), Decimal("1.5"), None)
    assert half_up.fee_paise == 2
    assert half_up.transfer_paise == 98

    odd = split_platform_fee(Decimal("100.01"), Decimal("2.5"), None)
    assert odd.total_paise == 10001
    assert odd.fee_paise == 250
    assert odd.transfer_paise == 9751
    assert odd.fee_paise + odd.transfer_paise == odd.total_paise

    both = split_platform_fee(Decimal("10.00"), Decimal("2.5"), Decimal("1.50"))
    assert both.fee_paise == 25 + 150
    assert both.transfer_paise == 1000 - 175

    dust = split_platform_fee(Decimal("0.01"), Decimal("1"), None)
    assert dust.fee_paise == 0
    assert dust.transfer_paise == 1

    third = split_platform_fee(Decimal("10.00"), Decimal("33.33"), None)
    assert third.fee_paise == 333
    assert third.transfer_paise == 667

    with pytest.raises(PlatformFeeError):
        split_platform_fee(Decimal("10.00"), Decimal("100"), Decimal("0.01"))
    with pytest.raises(PlatformFeeError):
        split_platform_fee(Decimal("10.00"), Decimal("101"), None)
    with pytest.raises(PlatformFeeError):
        split_platform_fee(Decimal("10.00"), Decimal("0"), Decimal("-1"))


def test_inactive_route_blocks_without_platform_fallback():
    with pytest.raises(PlatformFeeError, match="pay at the school office"):
        plan_razorpay_transfers(_settings(), Decimal("100.00"), "INR", {"invoice_id": "inv"})

    suspended = _settings(route_status="suspended", linked_account_id="acc_1")
    with pytest.raises(PlatformFeeError):
        plan_razorpay_transfers(suspended, Decimal("50.00"), "INR", {})

    allowed = _settings(allow_platform_settlement=True)
    transfers, split = plan_razorpay_transfers(allowed, Decimal("50.00"), "INR", {})
    assert transfers is None and split is None

    mock = _settings(gateway_provider="mock")
    transfers, split = plan_razorpay_transfers(mock, Decimal("50.00"), "INR", {})
    assert transfers is None and split is None


def test_activated_route_builds_transfer_in_paise():
    settings = _settings(
        route_status="activated",
        linked_account_id="acc_school",
        platform_fee_percent=Decimal("2.5"),
        platform_fee_fixed=Decimal("1.00"),
        settlement_on_hold=True,
    )
    transfers, split = plan_razorpay_transfers(
        settings, Decimal("100.00"), "INR", {"invoice_id": "inv-1", "empty": None}
    )
    assert split is not None and transfers is not None
    assert split.fee_paise == 250 + 100
    assert transfers[0]["account"] == "acc_school"
    assert transfers[0]["amount"] == split.transfer_paise
    assert transfers[0]["currency"] == "INR"
    assert transfers[0]["on_hold"] is True
    assert transfers[0]["notes"] == {"invoice_id": "inv-1"}
    assert split.transfer_paise + split.fee_paise == 10000


@pytest.mark.asyncio
async def test_order_posts_transfer_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "order_route_1", "amount": 10000, "currency": "INR"})

    gateway = RazorpayGateway(transport=httpx.MockTransport(handler))
    gateway.key_id = "rzp_test_key"
    gateway.key_secret = "rzp_test_secret"
    order = await gateway.create_order(
        amount=Decimal("100.00"),
        currency="INR",
        receipt="PAY-1",
        notes={"invoice_id": "inv"},
        transfers=[
            {
                "account": "acc_school",
                "amount": 9750,
                "currency": "INR",
                "notes": {"invoice_id": "inv"},
                "on_hold": False,
            }
        ],
    )
    assert order.order_id == "order_route_1"
    assert seen["path"] == "/v1/orders"
    assert seen["body"]["amount"] == 10000
    assert seen["body"]["transfers"][0]["account"] == "acc_school"
    assert seen["body"]["transfers"][0]["amount"] == 9750
    assert seen["auth"]


@pytest.mark.asyncio
async def test_refund_posts_reverse_all():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "rfnd_1", "amount": 10000})

    gateway = RazorpayGateway(transport=httpx.MockTransport(handler))
    gateway.key_id = "rzp_test_key"
    gateway.key_secret = "rzp_test_secret"
    result = await gateway.refund(payment_id="pay_1", amount=Decimal("100.00"), reverse_all=True)
    assert result.refund_id == "rfnd_1"
    assert seen["path"] == "/v1/payments/pay_1/refund"
    assert seen["body"] == {"amount": 10000, "reverse_all": True}


def _signed(body: bytes, secret: str = "whsec_test") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_signature_and_route_events(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test")
    gateway = RazorpayGateway()

    captured = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_1",
                    "order_id": "order_1",
                    "status": "captured",
                    "amount": 10000,
                    "currency": "INR",
                }
            }
        },
    }
    raw = json.dumps(captured).encode()
    ok = await gateway.handle_webhook(
        payload=json.loads(raw),
        headers={"x-razorpay-signature": _signed(raw)},
        raw_body=raw,
    )
    assert ok.success is True and ok.credit is True and ok.order_id == "order_1"

    bad = await gateway.handle_webhook(
        payload=json.loads(raw),
        headers={"x-razorpay-signature": "nope"},
        raw_body=raw,
    )
    assert bad.success is False and bad.credit is False

    failed_transfer = {
        "event": "transfer.failed",
        "account_id": "acc_school",
        "payload": {
            "transfer": {
                "entity": {
                    "id": "trf_1",
                    "amount": 9750,
                    "currency": "INR",
                    "status": "failed",
                    "source": "pay_1",
                    "recipient": "acc_school",
                    "error": {"description": "Account inactive"},
                }
            }
        },
    }
    raw_failed = json.dumps(failed_transfer).encode()
    parsed = await gateway.handle_webhook(
        payload=json.loads(raw_failed),
        headers={"x-razorpay-signature": _signed(raw_failed)},
        raw_body=raw_failed,
    )
    assert parsed.success is True
    assert parsed.credit is False
    assert parsed.kind == "transfer"
    assert parsed.transfer_status == "failed"
    assert parsed.failure_reason == "Account inactive"
    described = describe_webhook(failed_transfer)
    assert described["payment_id"] == "pay_1"

    replay = describe_webhook(failed_transfer)
    assert replay == described


def test_transfer_status_does_not_move_backwards():
    assert merge_transfer_status("processed", "created") == "processed"
    assert merge_transfer_status("processed", "failed") == "failed"
    assert merge_transfer_status("settled", "processed") == "settled"
    assert merge_transfer_status("reversed", "processed") == "reversed"
    assert merge_transfer_status("failed", "processed") == "processed"


def test_onboarding_state_machine():
    assert_can_create_account("not_started", None)
    with pytest.raises(RouteStateError):
        assert_can_create_account("created", "acc_1")
    with pytest.raises(RouteStateError):
        assert_can_add_stakeholder("not_started", None)
    assert_can_add_stakeholder("created", "acc_1")
    with pytest.raises(RouteStateError):
        assert_can_configure_product("created", "acc_1", None)
    assert_can_configure_product("needs_clarification", "acc_1", "sth_1")
    with pytest.raises(RouteStateError):
        assert_can_configure_product("suspended", "acc_1", "sth_1")


@pytest.mark.asyncio
async def test_onboarding_http_calls():
    calls: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/v2/accounts":
            return httpx.Response(200, json={"id": "acc_1", "status": "created"})
        if request.url.path.endswith("/stakeholders"):
            return httpx.Response(200, json={"id": "sth_1"})
        if request.url.path.endswith("/products") and request.method == "POST":
            return httpx.Response(200, json={"id": "acc_prd_1", "activation_status": "created"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "acc_prd_1", "activation_status": "under_review"})
        return httpx.Response(200, json={"id": "acc_1", "activation_status": "activated"})

    client = RazorpayRouteClient("key", "secret", transport=httpx.MockTransport(handler))
    account = RouteAccountIn(
        email="office@school.example",
        phone="9000090000",
        legal_business_name="Sybeez Demo School",
        contact_name="Principal",
        street1="1 MG Road",
        city="Bengaluru",
        state="KARNATAKA",
        postal_code="560001",
        pan="ABCDE1234F",
    )
    created = await client.create_account(account_payload(account, str(uuid4())))
    assert created["id"] == "acc_1"
    assert calls[0][0] == "POST" and calls[0][1] == "/v2/accounts"
    assert calls[0][2]["type"] == "route"
    assert calls[0][2]["legal_info"]["pan"] == "ABCDE1234F"
    assert "account_number" not in json.dumps(calls[0][2])

    stakeholder = RouteStakeholderIn(
        name="Principal",
        email="principal@school.example",
        street="1 MG Road",
        city="Bengaluru",
        state="KARNATAKA",
        postal_code="560001",
    )
    await client.create_stakeholder("acc_1", stakeholder_payload(stakeholder))
    assert calls[1][1] == "/v2/accounts/acc_1/stakeholders"

    settlement = RouteSettlementIn(
        account_number="123456789012",
        ifsc_code="HDFC0000053",
        beneficiary_name="Sybeez Demo School",
        tnc_accepted=True,
    )
    await client.create_product("acc_1", {"product_name": "route", "tnc_accepted": True})
    await client.update_product("acc_1", "acc_prd_1", settlement_patch(settlement))
    assert calls[2][2]["product_name"] == "route"
    assert calls[3][0] == "PATCH"
    assert calls[3][1] == "/v2/accounts/acc_1/products/acc_prd_1"
    assert calls[3][2]["settlements"]["ifsc_code"] == "HDFC0000053"
    assert calls[3][2]["settlements"]["account_number"] == "123456789012"


def test_bank_account_is_masked_and_encrypted(monkeypatch):
    monkeypatch.setenv("FEE_FIELD_KEY", "test-field-key-not-a-bank-account")
    monkeypatch.delenv("FEE_KMS_KEY_ID", raising=False)
    assert mask_account_number("123456789012") == "9012"
    token = encrypt_account_number("123456789012")
    assert token.startswith("fernet:")
    assert "123456789012" not in token
    assert decrypt_account_number(token) == "123456789012"


def test_kms_ciphertext_round_trip(monkeypatch):
    monkeypatch.setenv("FEE_KMS_KEY_ID", "alias/fee-fields")
    monkeypatch.setattr(
        "src.modules.fees.field_crypto._kms_encrypt",
        lambda key_id, plaintext: b"kms:" + plaintext,
    )
    monkeypatch.setattr(
        "src.modules.fees.field_crypto._kms_decrypt",
        lambda blob: blob[4:],
    )
    token = encrypt_account_number("9988776655")
    assert token.startswith("kms:")
    assert decrypt_account_number(token) == "9988776655"


def test_reconciliation_rows():
    matched, reason, expected = reconcile_payment(
        payment_status="SUCCESS",
        payment_amount=Decimal("100.00"),
        transfer_amount=Decimal("97.50"),
        platform_fee_amount=Decimal("2.50"),
        transfer_status="processed",
        allow_platform_settlement=False,
    )
    assert matched is True and reason == "matched" and expected == Decimal("97.50")

    failed, why, _ = reconcile_payment(
        payment_status="SUCCESS",
        payment_amount=Decimal("100.00"),
        transfer_amount=Decimal("97.50"),
        platform_fee_amount=Decimal("2.50"),
        transfer_status="failed",
        allow_platform_settlement=False,
    )
    assert failed is False and why == "transfer_failed"

    platform, why, _ = reconcile_payment(
        payment_status="SUCCESS",
        payment_amount=Decimal("100.00"),
        transfer_amount=None,
        platform_fee_amount=None,
        transfer_status=None,
        allow_platform_settlement=True,
    )
    assert platform is True and why == "platform_settlement"

    blocked, why, _ = reconcile_payment(
        payment_status="SUCCESS",
        payment_amount=Decimal("100.00"),
        transfer_amount=None,
        platform_fee_amount=None,
        transfer_status=None,
        allow_platform_settlement=False,
    )
    assert blocked is False and why == "missing_transfer"


def test_migration_is_0055_and_forces_rls():
    text = MIGRATION.read_text()
    assert 'revision: str = "0055"' in text
    assert 'down_revision: Union[str, None] = "0054"' in text
    assert "linked_account_id" in text
    assert "platform_fee_percent" in text
    assert "platform_fee_fixed" in text
    assert "settlement_on_hold" in text
    assert "allow_platform_settlement" in text
    assert "fee_route_transfers" in text
    assert "FORCE ROW LEVEL SECURITY" in text
    assert "fee_route_transfers_school" in text
    assert "fee_route_transfers_role" in text
    assert "app.can_see('finance'" in text
    assert "app.school_id" in text
    heads = list(Path(MIGRATION).parent.glob("*.py"))
    revisions = []
    for path in heads:
        for line in path.read_text().splitlines():
            if line.startswith("revision"):
                revisions.append(line.split("=")[1].strip().strip('"').strip("'"))
                break
    assert revisions.count("0054") == 1
    assert revisions.count("0055") == 1
    assert "0051" not in revisions
    assert "0052" not in revisions


def test_webhook_event_id_is_stable_for_replays():
    from src.modules.fees.payment_service import PaymentService

    payload = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_1"}}}}
    raw = json.dumps(payload, sort_keys=True).encode()
    service = PaymentService.__new__(PaymentService)
    first = service._event_id({"x-razorpay-event-id": "evt_1"}, raw, payload)
    second = service._event_id({"x-razorpay-event-id": "evt_1"}, raw, payload)
    assert first == second == "evt_1"
    digest = service._event_id({}, raw, payload)
    assert digest == hashlib.sha256(raw).hexdigest()


def test_route_terms_and_ifsc_rejected():
    with pytest.raises(ValueError):
        RouteSettlementIn(
            account_number="123456789",
            ifsc_code="HDFC0000053",
            beneficiary_name="School",
            tnc_accepted=False,
        )
    with pytest.raises(AppError):
        encrypt_account_number("12")


def test_blocked_online_message_is_parent_friendly():
    from src.modules.fees.route_money import BLOCKED_ONLINE

    assert "Sybeez" not in BLOCKED_ONLINE and "Route" not in BLOCKED_ONLINE
    assert "school office" in BLOCKED_ONLINE
