"""Postgres checks: RLS, composite foreign keys, receipt numbers, webhook crediting."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from src.core.database import AsyncSessionLocal
from src.core.exceptions import AppError, NotFoundError
from src.db.models import AcademicYear, School, Student, User
from src.db.models.fee import FeeCategory, FeeInvoice, FeeReceipt, FeeSequence, Payment, StudentFeeAccount
from src.db.models.finance import FeeLedgerEntry, FeePaymentTender, SchoolBranch
from src.modules.fees.numbering import allocate_series_number
from src.modules.fees.payment_service import PaymentService
from src.modules.fees.tenant import bind_tenant
from src.shared.enums import FeeInvoiceStatus, PaymentMethod, PaymentStatus

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    """The app engine is process-global; drop pooled connections before the loop closes."""
    yield
    from src.core.database import engine

    await engine.dispose()


async def _wipe(school_ids: list, user_ids: list) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("RESET ROLE"))
        await db.execute(text("SET session_replication_role = replica"))
        await db.execute(text("DELETE FROM fee_webhook_events WHERE event_id LIKE 'evt-%'"))
        for school_id in school_ids:
            await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": school_id})
        for user_id in user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await db.execute(text("SET session_replication_role = origin"))
        await db.execute(text("SELECT set_config('app.school_id', '', false)"))
        await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
        await db.commit()


async def _school(db, name: str) -> tuple[School, User, Student, AcademicYear, StudentFeeAccount]:
    user = User(
        id=uuid4(),
        email=f"{uuid4().hex}@finance.test",
        full_name=name,
        status="ACTIVE",
        is_active=True,
        is_platform_admin=False,
    )
    school = School(
        id=uuid4(),
        name=name,
        slug=f"fin-{uuid4().hex[:16]}",
        status="ACTIVE",
        timezone="Asia/Kolkata",
        currency="INR",
    )
    db.add_all([user, school])
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
    return school, user, student, year, account


def _invoice(school_id, account_id, number: str, total: str = "1000.00") -> FeeInvoice:
    amount = Decimal(total)
    return FeeInvoice(
        id=uuid4(),
        school_id=school_id,
        student_fee_account_id=account_id,
        invoice_number=number,
        invoice_date=date(2026, 4, 10),
        due_date=date(2026, 4, 20),
        currency="INR",
        subtotal=amount,
        discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        total_amount=amount,
        paid_amount=Decimal("0"),
        balance_amount=amount,
        status=FeeInvoiceStatus.ISSUED.value,
    )


def _payment(school_id, account_id, invoice_id, number: str, order_id: str) -> Payment:
    return Payment(
        id=uuid4(),
        school_id=school_id,
        student_fee_account_id=account_id,
        invoice_id=invoice_id,
        payment_number=number,
        amount=Decimal("1000.00"),
        currency="INR",
        method=PaymentMethod.ONLINE.value,
        status=PaymentStatus.PENDING.value,
        gateway="mock",
        gateway_order_id=order_id,
    )


async def test_rls_hides_other_schools_and_blocks_cross_writes():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school_a, user_a, *_ = await _school(db, "RLS A")
            school_b, user_b, *_ = await _school(db, "RLS B")
            school_ids += [school_a.id, school_b.id]
            user_ids += [user_a.id, user_b.id]
            db.add(FeeCategory(id=uuid4(), school_id=school_a.id, name="Tuition", code="TUI", active=True))
            db.add(FeeCategory(id=uuid4(), school_id=school_b.id, name="Tuition", code="TUI", active=True))
            await db.commit()

        async with AsyncSessionLocal() as db:
            await db.execute(text("SET ROLE sybeez_app"))
            await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
            await db.execute(
                text("SELECT set_config('app.school_id', :sid, false)"),
                {"sid": str(school_ids[0])},
            )
            visible = (await db.execute(select(FeeCategory.school_id))).scalars().all()
            assert visible
            assert set(visible) == {school_ids[0]}

            await db.execute(text("SELECT set_config('app.school_id', '', false)"))
            assert (await db.execute(select(FeeCategory.id))).scalars().all() == []

            with pytest.raises(DBAPIError) as exc:
                async with db.begin_nested():
                    db.add(
                        FeeCategory(
                            id=uuid4(),
                            school_id=school_ids[1],
                            name="Hack",
                            code="HACK",
                            active=True,
                        )
                    )
                    await db.flush()
            assert "row-level security" in str(exc.value).lower()
            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.execute(text("SELECT set_config('app.school_id', '', false)"))
            await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids)


async def test_composite_fk_rejects_cross_tenant_tender():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school_a, user_a, _, _, account_a = await _school(db, "FK A")
            school_b, user_b, *_ = await _school(db, "FK B")
            school_ids += [school_a.id, school_b.id]
            user_ids += [user_a.id, user_b.id]
            branch_b = SchoolBranch(
                id=uuid4(), school_id=school_b.id, name="West", code="WEST", active=True
            )
            invoice = _invoice(school_a.id, account_a.id, f"INV-{uuid4().hex[:8]}")
            payment = _payment(
                school_a.id, account_a.id, invoice.id, f"PAY-{uuid4().hex[:8]}", f"mock_order_{uuid4().hex[:8]}"
            )
            db.add_all([branch_b, invoice, payment])
            await db.flush()
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    db.add(
                        FeePaymentTender(
                            id=uuid4(),
                            school_id=school_b.id,
                            payment_id=payment.id,
                            branch_id=branch_b.id,
                            method="CASH",
                            amount=Decimal("10.00"),
                            status="SETTLED",
                        )
                    )
                    await db.flush()
            await db.rollback()
    finally:
        await _wipe(school_ids, user_ids)


async def test_service_lookup_does_not_return_other_school_receipt():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school_a, user_a, _, _, account = await _school(db, "Svc A")
            school_b, user_b, *_rest = await _school(db, "Svc B")
            school_ids += [school_a.id, school_b.id]
            user_ids += [user_a.id, user_b.id]
            invoice = _invoice(school_a.id, account.id, f"INV-{uuid4().hex[:8]}")
            payment = _payment(
                school_a.id, account.id, invoice.id, f"PAY-{uuid4().hex[:8]}", f"mock_order_{uuid4().hex[:8]}"
            )
            receipt = FeeReceipt(
                id=uuid4(),
                school_id=school_a.id,
                payment_id=payment.id,
                receipt_number=f"REC-{uuid4().hex[:8]}",
                issued_at=datetime.now(timezone.utc),
                status="ACTIVE",
                reprint_count=0,
            )
            db.add_all([invoice, payment])
            await db.flush()
            db.add(receipt)
            await db.commit()
            await bind_tenant(db, school_b.id)
            with pytest.raises(NotFoundError):
                await PaymentService(db).get_receipt(school_b.id, receipt.id)
    finally:
        await _wipe(school_ids, user_ids)


async def test_receipt_numbers_are_gap_free_per_financial_year():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school, user, *_ = await _school(db, "Numbers")
            school_ids.append(school.id)
            user_ids.append(user.id)
            await db.commit()
            school_id = school.id

        async def take(on: date) -> str:
            async with AsyncSessionLocal() as db:
                number = await allocate_series_number(db, school_id, "RECEIPT", "REC", on=on)
                await db.commit()
                return number

        march = await asyncio.gather(*[take(date(2026, 3, 31)) for _ in range(4)])
        april = await asyncio.gather(*[take(date(2026, 4, 1)) for _ in range(4)])
        assert sorted(march) == [f"REC-2025-26-{i:06d}" for i in range(1, 5)]
        assert sorted(april) == [f"REC-2026-27-{i:06d}" for i in range(1, 5)]
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(select(FeeSequence).where(FeeSequence.school_id == school_id))
            ).scalars().all()
            assert {r.fy_label: r.last_value for r in rows} == {"2025-26": 4, "2026-27": 4}
    finally:
        await _wipe(school_ids, user_ids)


async def _clear_webhook_events() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM fee_webhook_events WHERE event_id LIKE 'evt-%'"))
        await db.commit()


async def _webhook(
    order_id: str,
    event: str,
    event_id: str,
    amount: str = "1000.00",
    sig: str = "mock_secret",
    payment_id: str = "mock_pay_captured",
):
    payload = {
        "order_id": order_id,
        "payment_id": payment_id,
        "amount": amount,
        "currency": "INR",
        "event": event,
        "status": "failed" if event == "payment.failed" else "captured",
    }
    headers = {"x-mock-signature": sig, "x-mock-event-id": event_id}
    async with AsyncSessionLocal() as db:
        return await PaymentService(db).handle_webhook("mock", payload, headers)


async def test_webhook_credits_only_captured_and_is_idempotent():
    school_ids: list = []
    user_ids: list = []
    await _clear_webhook_events()
    try:
        async with AsyncSessionLocal() as db:
            school, user, _, _, account = await _school(db, "Webhook")
            school_ids.append(school.id)
            user_ids.append(user.id)
            invoice = _invoice(school.id, account.id, f"INV-{uuid4().hex[:8]}")
            order_id = f"mock_order_{uuid4().hex[:10]}"
            payment = _payment(school.id, account.id, invoice.id, f"PAY-{uuid4().hex[:8]}", order_id)
            bad_invoice = _invoice(school.id, account.id, f"INV-{uuid4().hex[:8]}")
            bad_order = f"mock_order_{uuid4().hex[:10]}"
            bad_payment = _payment(
                school.id, account.id, bad_invoice.id, f"PAY-{uuid4().hex[:8]}", bad_order
            )
            db.add_all([invoice, payment, bad_invoice, bad_payment])
            await db.commit()
            invoice_id = invoice.id
            payment_id = payment.id

        with pytest.raises(AppError):
            await _webhook(bad_order, "payment.captured", "evt-bad-sig", sig="nope")

        pay_ref = f"mock_pay_{uuid4().hex[:10]}"
        ignored = await _webhook(order_id, "payment.failed", "evt-failed", payment_id=pay_ref)
        assert ignored["credited"] is False
        async with AsyncSessionLocal() as db:
            row = await db.get(Payment, payment_id)
            assert row.status == PaymentStatus.FAILED.value
            receipts = (
                await db.execute(select(func.count()).select_from(FeeReceipt).where(FeeReceipt.payment_id == payment_id))
            ).scalar_one()
            assert receipts == 0
            inv = await db.get(FeeInvoice, invoice_id)
            assert inv.paid_amount == Decimal("0.00")

        first = await _webhook(order_id, "payment.captured", "evt-cap-1", payment_id=pay_ref)
        assert first.receipt_number.startswith("REC-")
        second = await _webhook(order_id, "payment.captured", "evt-cap-1", payment_id=pay_ref)
        assert second.receipt_number == first.receipt_number
        third = await _webhook(order_id, "payment.captured", "evt-cap-2", payment_id=pay_ref)
        assert third.receipt_number == first.receipt_number

        async with AsyncSessionLocal() as db:
            inv = await db.get(FeeInvoice, invoice_id)
            assert inv.paid_amount == Decimal("1000.00")
            assert inv.balance_amount == Decimal("0.00")
            count = (
                await db.execute(select(func.count()).select_from(FeeReceipt).where(FeeReceipt.payment_id == payment_id))
            ).scalar_one()
            assert count == 1

        mismatch_order = bad_order
        with pytest.raises(AppError):
            await _webhook(
                mismatch_order,
                "payment.captured",
                "evt-mismatch",
                amount="1.00",
                payment_id=f"mock_pay_{uuid4().hex[:10]}",
            )
        async with AsyncSessionLocal() as db:
            inv = await db.get(FeeInvoice, bad_invoice.id)
            assert inv.paid_amount == Decimal("0.00")
    finally:
        await _wipe(school_ids, user_ids)


async def test_concurrent_capture_does_not_double_credit():
    school_ids: list = []
    user_ids: list = []
    await _clear_webhook_events()
    try:
        async with AsyncSessionLocal() as db:
            school, user, _, _, account = await _school(db, "Race")
            school_ids.append(school.id)
            user_ids.append(user.id)
            invoice = _invoice(school.id, account.id, f"INV-{uuid4().hex[:8]}")
            order_id = f"mock_order_{uuid4().hex[:10]}"
            payment = _payment(school.id, account.id, invoice.id, f"PAY-{uuid4().hex[:8]}", order_id)
            db.add_all([invoice, payment])
            await db.commit()
            invoice_id = invoice.id
            payment_id = payment.id

        pay_ref = f"mock_pay_{uuid4().hex[:10]}"
        await asyncio.gather(
            _webhook(order_id, "payment.captured", "evt-race-1", payment_id=pay_ref),
            _webhook(order_id, "payment.captured", "evt-race-2", payment_id=pay_ref),
        )
        async with AsyncSessionLocal() as db:
            inv = await db.get(FeeInvoice, invoice_id)
            assert inv.paid_amount == Decimal("1000.00")
            count = (
                await db.execute(select(func.count()).select_from(FeeReceipt).where(FeeReceipt.payment_id == payment_id))
            ).scalar_one()
            assert count == 1
    finally:
        await _wipe(school_ids, user_ids)


async def test_ledger_is_append_only():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school, user, student, _, account = await _school(db, "Ledger")
            school_ids.append(school.id)
            user_ids.append(user.id)
            entry = FeeLedgerEntry(
                id=uuid4(),
                school_id=school.id,
                student_id=student.id,
                account_id=account.id,
                entry_date=date(2026, 4, 1),
                entry_type="DEMAND",
                debit=Decimal("10.00"),
                credit=Decimal("0"),
                ref_type="test",
                ref_id=uuid4(),
                narration="opening",
                created_at=datetime.now(timezone.utc),
            )
            db.add(entry)
            await db.commit()
            entry_id = entry.id
        async with AsyncSessionLocal() as db:
            row = await db.get(FeeLedgerEntry, entry_id)
            row.narration = "tamper"
            with pytest.raises(DBAPIError) as exc:
                await db.commit()
            assert "append-only" in str(exc.value).lower()
            await db.rollback()
    finally:
        await _wipe(school_ids, user_ids)
