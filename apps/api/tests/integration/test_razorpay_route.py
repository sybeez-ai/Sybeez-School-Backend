"""Razorpay Route webhooks, refund reversal, and school isolation."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from src.core.database import AsyncSessionLocal
from src.db.models import AcademicYear, School, Student, User
from src.db.models.fee import FeeInvoice, FeeReceipt, Payment, PaymentRefund, StudentFeeAccount
from src.db.models.finance import FeeLedgerEntry, FeeRouteTransfer, FeeWebhookEvent
from src.modules.fees.gateways.base import GatewayRefundResult
from src.modules.fees.gateways.razorpay import RazorpayGateway
from src.modules.fees.payment_service import PaymentService
from src.modules.fees.schemas import RefundIn
from src.shared.enums import FeeInvoiceStatus, LedgerEntryType, PaymentMethod, PaymentStatus, RefundStatus

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


async def _wipe(school_ids: list, user_ids: list) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("RESET ROLE"))
        await db.execute(text("SET session_replication_role = replica"))
        await db.execute(text("DELETE FROM fee_webhook_events WHERE event_id LIKE 'evt-route-%'"))
        for school_id in school_ids:
            await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": school_id})
        for user_id in user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await db.execute(text("SET session_replication_role = origin"))
        await db.execute(text("SELECT set_config('app.school_id', '', false)"))
        await db.execute(text("SELECT set_config('app.user_id', '', false)"))
        await db.execute(text("SELECT set_config('app.role', '', false)"))
        await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
        await db.commit()


async def _school(db, name: str):
    user = User(
        id=uuid4(),
        email=f"{uuid4().hex}@route.test",
        full_name=name,
        status="ACTIVE",
        is_active=True,
        is_platform_admin=False,
    )
    approver = User(
        id=uuid4(),
        email=f"{uuid4().hex}@route.test",
        full_name=f"{name} checker",
        status="ACTIVE",
        is_active=True,
        is_platform_admin=False,
    )
    school = School(
        id=uuid4(),
        name=name,
        slug=f"route-{uuid4().hex[:16]}",
        status="ACTIVE",
        timezone="Asia/Kolkata",
        currency="INR",
    )
    db.add_all([user, approver, school])
    await db.flush()
    year = AcademicYear(
        id=uuid4(),
        school_id=school.id,
        name="2026-27",
        starts_on=date(2026, 4, 1),
        ends_on=date(2027, 3, 31),
        is_current=True,
    )
    student = Student(id=uuid4(), school_id=school.id, user_id=user.id, status="ACTIVE")
    db.add_all([year, student])
    await db.flush()
    account = StudentFeeAccount(
        id=uuid4(),
        school_id=school.id,
        student_id=student.id,
        academic_year_id=year.id,
        status="ACTIVE",
    )
    db.add(account)
    await db.flush()
    return school, user, approver, account


def _signed(payload: dict, secret: str = "whsec_route") -> tuple[bytes, dict]:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"x-razorpay-signature": signature}


async def test_capture_is_idempotent_and_transfer_failure_keeps_the_receipt(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_route")
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school, user, approver, account = await _school(db, "Route Capture")
            school_ids.append(school.id)
            user_ids.extend([user.id, approver.id])
            invoice = FeeInvoice(
                id=uuid4(),
                school_id=school.id,
                student_fee_account_id=account.id,
                invoice_number=f"INV-{uuid4().hex[:8]}",
                invoice_date=date(2026, 4, 10),
                due_date=date(2026, 4, 20),
                currency="INR",
                subtotal=Decimal("1000.00"),
                discount_amount=Decimal("0"),
                tax_amount=Decimal("0"),
                total_amount=Decimal("1000.00"),
                paid_amount=Decimal("0"),
                balance_amount=Decimal("1000.00"),
                status=FeeInvoiceStatus.ISSUED.value,
            )
            order_id = f"order_{uuid4().hex[:10]}"
            payment = Payment(
                id=uuid4(),
                school_id=school.id,
                student_fee_account_id=account.id,
                invoice_id=invoice.id,
                payment_number=f"PAY-{uuid4().hex[:8]}",
                amount=Decimal("1000.00"),
                currency="INR",
                method=PaymentMethod.ONLINE.value,
                status=PaymentStatus.PENDING.value,
                gateway="razorpay",
                gateway_order_id=order_id,
            )
            transfer = FeeRouteTransfer(
                id=uuid4(),
                school_id=school.id,
                payment_id=payment.id,
                linked_account_id="acc_school",
                gateway_order_id=order_id,
                currency="INR",
                payment_amount=Decimal("1000.00"),
                transfer_amount=Decimal("975.00"),
                platform_fee_amount=Decimal("25.00"),
                on_hold=False,
                status="created",
            )
            db.add_all([invoice, payment, transfer])
            await db.commit()
            payment_id = payment.id
            invoice_id = invoice.id

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_route_1",
                        "order_id": order_id,
                        "status": "captured",
                        "amount": 100000,
                        "currency": "INR",
                    }
                }
            },
        }
        raw, headers = _signed(payload)
        headers["x-razorpay-event-id"] = "evt-route-cap"
        async with AsyncSessionLocal() as db:
            first = await PaymentService(db).handle_webhook("razorpay", json.loads(raw), headers, raw)
        async with AsyncSessionLocal() as db:
            second = await PaymentService(db).handle_webhook("razorpay", json.loads(raw), headers, raw)
        assert first.receipt_number == second.receipt_number

        failed = {
            "event": "transfer.failed",
            "account_id": "acc_school",
            "payload": {
                "transfer": {
                    "entity": {
                        "id": "trf_fail",
                        "source": "pay_route_1",
                        "recipient": "acc_school",
                        "amount": 97500,
                        "currency": "INR",
                        "status": "failed",
                        "error": {"description": "beneficiary mismatch"},
                    }
                }
            },
        }
        raw_failed, failed_headers = _signed(failed)
        failed_headers["x-razorpay-event-id"] = "evt-route-trf"
        async with AsyncSessionLocal() as db:
            outcome = await PaymentService(db).handle_webhook(
                "razorpay", json.loads(raw_failed), failed_headers, raw_failed
            )
        assert outcome["transfer_status"] == "failed"
        async with AsyncSessionLocal() as db:
            again = await PaymentService(db).handle_webhook(
                "razorpay", json.loads(raw_failed), failed_headers, raw_failed
            )
            assert again["duplicate"] is True
            row = await db.get(Payment, payment_id)
            assert row.status == PaymentStatus.SUCCESS.value
            assert row.gateway_payment_id == "pay_route_1"
            receipts = (
                await db.execute(select(func.count()).select_from(FeeReceipt).where(FeeReceipt.payment_id == payment_id))
            ).scalar_one()
            assert receipts == 1
            invoice_row = await db.get(FeeInvoice, invoice_id)
            assert invoice_row.paid_amount == Decimal("1000.00")
            transfer_row = (
                await db.execute(select(FeeRouteTransfer).where(FeeRouteTransfer.payment_id == payment_id))
            ).scalar_one()
            assert transfer_row.status == "failed"
            assert transfer_row.failure_reason == "beneficiary mismatch"
            credits = (
                await db.execute(
                    select(func.count())
                    .select_from(FeeLedgerEntry)
                    .where(
                        FeeLedgerEntry.ref_id == payment_id,
                        FeeLedgerEntry.entry_type == LedgerEntryType.PAYMENT.value,
                    )
                )
            ).scalar_one()
            assert credits == 1
    finally:
        await _wipe(school_ids, user_ids)


async def test_refund_reverses_the_school_transfer_once(monkeypatch):
    school_ids: list = []
    user_ids: list = []
    seen: dict = {}

    async def _refund(self, *, payment_id: str, amount: Decimal, reverse_all: bool = False):
        del self, payment_id
        seen["reverse_all"] = reverse_all
        seen["amount"] = amount
        return GatewayRefundResult(success=True, refund_id="rfnd_route_1", amount=amount)

    monkeypatch.setattr(RazorpayGateway, "refund", _refund)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_route")
    try:
        async with AsyncSessionLocal() as db:
            school, user, approver, account = await _school(db, "Route Refund")
            school_ids.append(school.id)
            user_ids.extend([user.id, approver.id])
            invoice = FeeInvoice(
                id=uuid4(),
                school_id=school.id,
                student_fee_account_id=account.id,
                invoice_number=f"INV-{uuid4().hex[:8]}",
                invoice_date=date(2026, 4, 10),
                due_date=date(2026, 4, 20),
                currency="INR",
                subtotal=Decimal("1000.00"),
                discount_amount=Decimal("0"),
                tax_amount=Decimal("0"),
                total_amount=Decimal("1000.00"),
                paid_amount=Decimal("1000.00"),
                balance_amount=Decimal("0.00"),
                status=FeeInvoiceStatus.PAID.value,
            )
            payment = Payment(
                id=uuid4(),
                school_id=school.id,
                student_fee_account_id=account.id,
                invoice_id=invoice.id,
                payment_number=f"PAY-{uuid4().hex[:8]}",
                amount=Decimal("1000.00"),
                currency="INR",
                method=PaymentMethod.ONLINE.value,
                status=PaymentStatus.SUCCESS.value,
                gateway="razorpay",
                gateway_order_id="order_refund_1",
                gateway_payment_id="pay_refund_1",
                paid_at=datetime.now(timezone.utc),
            )
            transfer = FeeRouteTransfer(
                id=uuid4(),
                school_id=school.id,
                payment_id=payment.id,
                linked_account_id="acc_school",
                gateway_order_id="order_refund_1",
                gateway_payment_id="pay_refund_1",
                currency="INR",
                payment_amount=Decimal("1000.00"),
                transfer_amount=Decimal("1000.00"),
                platform_fee_amount=Decimal("0.00"),
                status="processed",
            )
            db.add_all([invoice, payment, transfer])
            await db.commit()
            school_id, user_id, approver_id = school.id, user.id, approver.id
            payment_id, invoice_id = payment.id, invoice.id

        async with AsyncSessionLocal() as db:
            requested = await PaymentService(db).request_refund(
                school_id, user_id, payment_id, RefundIn(amount=Decimal("1000.00"), reason="duplicate")
            )
        async with AsyncSessionLocal() as db:
            approved = await PaymentService(db).approve_refund(school_id, approver_id, requested.id)
        assert seen["reverse_all"] is True
        assert approved.status == RefundStatus.SUCCESS.value
        assert approved.gateway_refund_id == "rfnd_route_1"

        refund_event = {
            "event": "refund.processed",
            "payload": {
                "refund": {"entity": {"id": "rfnd_route_1", "payment_id": "pay_refund_1", "amount": 100000, "status": "processed"}},
                "payment": {"entity": {"id": "pay_refund_1", "order_id": "order_refund_1", "amount": 100000, "currency": "INR"}},
            },
        }
        raw, headers = _signed(refund_event)
        headers["x-razorpay-event-id"] = "evt-route-rfnd"
        async with AsyncSessionLocal() as db:
            await PaymentService(db).handle_webhook("razorpay", json.loads(raw), headers, raw)
            await PaymentService(db).handle_webhook("razorpay", json.loads(raw), headers, raw)

        async with AsyncSessionLocal() as db:
            invoice_row = await db.get(FeeInvoice, invoice_id)
            assert invoice_row.paid_amount == Decimal("0.00")
            assert invoice_row.balance_amount == Decimal("1000.00")
            transfer_row = (
                await db.execute(select(FeeRouteTransfer).where(FeeRouteTransfer.payment_id == payment_id))
            ).scalar_one()
            assert transfer_row.status == "reversed"
            refunds = (
                await db.execute(select(func.count()).select_from(PaymentRefund).where(PaymentRefund.payment_id == payment_id))
            ).scalar_one()
            assert refunds == 1
            ledger = (
                await db.execute(
                    select(func.count())
                    .select_from(FeeLedgerEntry)
                    .where(
                        FeeLedgerEntry.school_id == school_id,
                        FeeLedgerEntry.entry_type == LedgerEntryType.REFUND.value,
                        FeeLedgerEntry.ref_type == "payment_refund",
                    )
                )
            ).scalar_one()
            assert ledger == 1
            events = (
                await db.execute(select(func.count()).select_from(FeeWebhookEvent).where(FeeWebhookEvent.event_id == "evt-route-rfnd"))
            ).scalar_one()
            assert events == 1
    finally:
        await _wipe(school_ids, user_ids)


async def test_route_transfers_are_isolated_by_school():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school_a, user_a, _, account_a = await _school(db, "Route A")
            school_b, user_b, _, account_b = await _school(db, "Route B")
            school_ids.extend([school_a.id, school_b.id])
            user_ids.extend([user_a.id, user_b.id])
            rows = []
            payment_b_id = None
            for school, account, label in (
                (school_a, account_a, "A"),
                (school_b, account_b, "B"),
            ):
                invoice = FeeInvoice(
                    id=uuid4(),
                    school_id=school.id,
                    student_fee_account_id=account.id,
                    invoice_number=f"INV-{label}-{uuid4().hex[:6]}",
                    invoice_date=date(2026, 4, 10),
                    due_date=date(2026, 4, 20),
                    currency="INR",
                    subtotal=Decimal("10.00"),
                    discount_amount=Decimal("0"),
                    tax_amount=Decimal("0"),
                    total_amount=Decimal("10.00"),
                    paid_amount=Decimal("0"),
                    balance_amount=Decimal("10.00"),
                    status=FeeInvoiceStatus.ISSUED.value,
                )
                payment = Payment(
                    id=uuid4(),
                    school_id=school.id,
                    student_fee_account_id=account.id,
                    invoice_id=invoice.id,
                    payment_number=f"PAY-{label}-{uuid4().hex[:6]}",
                    amount=Decimal("10.00"),
                    currency="INR",
                    method=PaymentMethod.ONLINE.value,
                    status=PaymentStatus.PENDING.value,
                    gateway="razorpay",
                    gateway_order_id=f"order_{label}_{uuid4().hex[:6]}",
                )
                rows.extend([invoice, payment])
                if label == "B":
                    payment_b_id = payment.id
                    continue
                rows.append(
                    FeeRouteTransfer(
                        id=uuid4(),
                        school_id=school.id,
                        payment_id=payment.id,
                        linked_account_id=f"acc_{label}",
                        currency="INR",
                        payment_amount=Decimal("10.00"),
                        transfer_amount=Decimal("10.00"),
                        platform_fee_amount=Decimal("0.00"),
                        status="created",
                    )
                )
            db.add_all(rows)
            await db.commit()
            school_a_id, school_b_id, user_a_id = school_a.id, school_b.id, user_a.id

        async with AsyncSessionLocal() as db:
            await db.execute(text("SET ROLE sybeez_app"))
            await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
            await db.execute(text("SELECT set_config('app.role', 'SCHOOL_ADMIN', false)"))
            await db.execute(text("SELECT set_config('app.user_id', :uid, false)"), {"uid": str(user_a_id)})
            await db.execute(text("SELECT set_config('app.school_id', :sid, false)"), {"sid": str(school_a_id)})
            visible = (await db.execute(select(FeeRouteTransfer.school_id))).scalars().all()
            assert visible == [school_a_id]

            await db.execute(text("SELECT set_config('app.role', '', false)"))
            assert (await db.execute(select(FeeRouteTransfer.id))).scalars().all() == []
            await db.execute(text("SELECT set_config('app.role', 'SCHOOL_ADMIN', false)"))
            await db.execute(text("SELECT set_config('app.school_id', '', false)"))
            assert (await db.execute(select(FeeRouteTransfer.id))).scalars().all() == []

            await db.execute(text("SELECT set_config('app.school_id', :sid, false)"), {"sid": str(school_a_id)})
            with pytest.raises(DBAPIError) as exc:
                async with db.begin_nested():
                    db.add(
                        FeeRouteTransfer(
                            id=uuid4(),
                            school_id=school_b_id,
                            payment_id=payment_b_id,
                            linked_account_id="acc_hack",
                            currency="INR",
                            payment_amount=Decimal("10.00"),
                            transfer_amount=Decimal("10.00"),
                            platform_fee_amount=Decimal("0.00"),
                            status="created",
                        )
                    )
                    await db.flush()
            assert "row-level security" in str(exc.value).lower()
            await db.rollback()
    finally:
        await _wipe(school_ids, user_ids)
