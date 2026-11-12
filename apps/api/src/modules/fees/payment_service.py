"""Payments, refunds, receipts, gateway verification."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    FeeInvoice,
    FeeReceipt,
    Parent,
    Payment,
    PaymentAllocation,
    PaymentRefund,
    Student,
    StudentParent,
    User,
)
from src.db.models.fee import StudentFeeAccount
from src.db.models.finance import FeeRouteTransfer, FeeWebhookEvent, FinanceApproval
from src.modules.fees.calculations import invoice_status_after_payment, money
from src.modules.fees.crediting import assert_distinct_checker, is_failure_event, webhook_should_credit
from src.modules.fees.gateways import get_gateway
from src.modules.fees.ledger import post_ledger_entry
from src.modules.fees.numbering import indian_financial_year
from src.modules.fees.permissions import FINANCE_COLLECT_VALUES, FINANCE_VIEW_VALUES
from src.modules.fees.route_money import PlatformFeeError, plan_razorpay_transfers
from src.modules.fees.route_state import merge_transfer_status
from src.modules.fees.route_webhooks import attach_captured_transfer, maybe_handle_route
from src.modules.fees.receipt_pdf import build_receipt_pdf
from src.modules.fees.reversal import reverse_payment_amount
from src.modules.fees.schemas import (
    ManualPaymentIn,
    OnlinePaymentRow,
    PaymentLinkIn,
    PaymentLinkOut,
    PaymentOrderIn,
    PaymentOrderOut,
    PaymentOut,
    PaymentVerifyIn,
    ReceiptOut,
    RefundIn,
    RefundOut,
)
from src.modules.fees.tenant import assert_same_school, bind_tenant, set_rls_bypass
from src.modules.fees.service import FeeService
from src.modules.notifications.service import create_in_app_notification
from src.shared.enums import (
    ApprovalStatus,
    FeeInvoiceStatus,
    FinanceApprovalType,
    LedgerEntryType,
    NotificationType,
    PaymentMethod,
    PaymentStatus,
    ReceiptStatus,
    RefundStatus,
    UserRole,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.fees = FeeService(db)

    async def _payment_out(self, p: Payment) -> PaymentOut:
        receipt = (
            await self.db.execute(select(FeeReceipt).where(FeeReceipt.payment_id == p.id))
        ).scalar_one_or_none()
        return PaymentOut(
            id=p.id,
            school_id=p.school_id,
            student_fee_account_id=p.student_fee_account_id,
            invoice_id=p.invoice_id,
            payment_number=p.payment_number,
            amount=money(p.amount),
            currency=p.currency,
            method=p.method,
            status=p.status,
            gateway=p.gateway,
            gateway_order_id=p.gateway_order_id,
            gateway_payment_id=p.gateway_payment_id,
            gateway_reference=p.gateway_reference,
            paid_at=p.paid_at,
            notes=p.notes,
            receipt_id=receipt.id if receipt else None,
            receipt_number=receipt.receipt_number if receipt else None,
        )

    async def _assert_parent_invoice(
        self, school_id: UUID, user_id: UUID, invoice: FeeInvoice
    ) -> None:
        acc = await self.db.get(StudentFeeAccount, invoice.student_fee_account_id)
        if not acc or acc.school_id != school_id:
            raise NotFoundError("Invoice not found")
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        link = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent.id,
                    StudentParent.student_id == acc.student_id,
                )
            )
        ).scalar_one_or_none()
        if not link:
            raise ForbiddenError("Not linked to this student")

    async def create_order(
        self,
        school_id: UUID,
        user_id: UUID,
        role: str,
        data: PaymentOrderIn,
        *,
        notes: str | None = None,
    ) -> PaymentOrderOut:
        inv = await self.db.get(FeeInvoice, data.invoice_id)
        if not inv or inv.school_id != school_id:
            raise NotFoundError("Invoice not found")
        if inv.status in (FeeInvoiceStatus.CANCELLED.value, FeeInvoiceStatus.PAID.value):
            raise AppError("Invoice is not payable", status_code=422)
        await bind_tenant(self.db, school_id)
        if role == UserRole.PARENT.value:
            await self._assert_parent_invoice(school_id, user_id, inv)
            # Ownership is proven above. The order writes touch school-level rows
            # (fee_settings, fee_sequences) and a new fee_payments row whose RLS
            # check cannot resolve its own student, so PARENT RLS rejects them.
            # Run the rest as SERVICE, still pinned to this school.
            await bind_tenant(self.db, school_id, role="SERVICE")
        elif role not in FINANCE_COLLECT_VALUES:
            raise ForbiddenError("Not allowed to create payment orders")

        if data.idempotency_key:
            existing = (
                await self.db.execute(
                    select(Payment).where(
                        Payment.school_id == school_id,
                        Payment.idempotency_key == data.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing and existing.gateway_order_id:
                settings = await self.fees.get_or_create_settings(school_id)
                return PaymentOrderOut(
                    payment_id=existing.id,
                    payment_number=existing.payment_number,
                    order_id=existing.gateway_order_id,
                    amount=money(existing.amount),
                    currency=existing.currency,
                    gateway=existing.gateway or "",
                    public_key=settings.gateway_public_key,
                )

        settings = await self.fees.get_or_create_settings(school_id)
        amount = money(data.amount) if data.amount is not None else money(inv.balance_amount)
        if amount <= 0:
            raise AppError("Amount must be positive", status_code=422)
        if amount > money(inv.balance_amount):
            raise AppError("Amount exceeds invoice balance", status_code=422)
        if not settings.allow_partial_payments and amount < money(inv.balance_amount):
            raise AppError("Partial payments disabled", status_code=422)

        core = get_settings()
        if (core.app_env or "").lower() == "production" and settings.gateway_provider == "mock":
            raise AppError("Online payments not configured for production", status_code=500)

        gateway = get_gateway(settings.gateway_provider)
        number = await self.fees.next_number(school_id, "PAYMENT", settings.payment_prefix)
        order_notes = {"invoice_id": str(inv.id), "school_id": str(school_id), "payment_number": number}
        try:
            transfers, split = plan_razorpay_transfers(
                settings, amount, inv.currency or settings.currency, order_notes
            )
        except PlatformFeeError as exc:
            raise AppError(str(exc), status_code=422) from exc
        order = await gateway.create_order(
            amount=amount,
            currency=inv.currency or settings.currency,
            receipt=number,
            notes=order_notes,
            transfers=transfers,
        )
        payment = Payment(
            id=uuid4(),
            school_id=school_id,
            student_fee_account_id=inv.student_fee_account_id,
            invoice_id=inv.id,
            payment_number=number,
            amount=amount,
            currency=inv.currency or settings.currency,
            method=PaymentMethod.ONLINE.value,
            status=PaymentStatus.PENDING.value,
            gateway=gateway.name,
            gateway_order_id=order.order_id,
            idempotency_key=data.idempotency_key,
            notes=notes,
        )
        self.db.add(payment)
        await self.db.flush()
        if split is not None and transfers:
            self.db.add(
                FeeRouteTransfer(
                    id=uuid4(),
                    school_id=school_id,
                    payment_id=payment.id,
                    linked_account_id=transfers[0]["account"],
                    gateway_order_id=order.order_id,
                    currency=payment.currency,
                    payment_amount=amount,
                    transfer_amount=split.transfer_amount,
                    platform_fee_amount=split.fee_amount,
                    on_hold=bool(transfers[0].get("on_hold")),
                    status="created",
                )
            )
        await self.db.commit()
        await self.db.refresh(payment)
        return PaymentOrderOut(
            payment_id=payment.id,
            payment_number=payment.payment_number,
            order_id=order.order_id,
            amount=amount,
            currency=payment.currency,
            gateway=gateway.name,
            public_key=settings.gateway_public_key,
            platform_fee_amount=split.fee_amount if split else None,
            transfer_amount=split.transfer_amount if split else None,
            route_account_id=transfers[0]["account"] if transfers else None,
        )

    async def create_payment_link(
        self, school_id: UUID, user_id: UUID, role: str, data: PaymentLinkIn
    ) -> PaymentLinkOut:
        """Gateway order the school can send to a parent. Scoped to this school."""
        order = await self.create_order(
            school_id,
            user_id,
            role,
            PaymentOrderIn(
                invoice_id=data.invoice_id,
                amount=data.amount,
                idempotency_key=data.idempotency_key,
            ),
            notes="payment_link",
        )
        payment = await self.db.get(Payment, order.payment_id)
        base = get_settings().web_app_url.rstrip("/")
        return PaymentLinkOut(
            payment_id=order.payment_id,
            payment_number=order.payment_number,
            invoice_id=data.invoice_id,
            order_id=order.order_id,
            amount=order.amount,
            currency=order.currency,
            gateway=order.gateway,
            url=f"{base}/pay/{order.order_id}",
            status=payment.status if payment and payment.school_id == school_id else PaymentStatus.PENDING.value,
        )

    async def reconcile_online_payments(
        self,
        school_id: UUID,
        *,
        status: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[OnlinePaymentRow]:
        """Online payments for this school only. RLS applies on top of the school filter."""
        await bind_tenant(self.db, school_id)
        q = select(Payment).where(
            Payment.school_id == school_id,
            Payment.method == PaymentMethod.ONLINE.value,
        )
        if status:
            q = q.where(Payment.status == status)
        payments = (
            await self.db.execute(q.order_by(Payment.created_at.desc()))
        ).scalars().all()
        transfer_rows = (
            await self.db.execute(
                select(FeeRouteTransfer).where(FeeRouteTransfer.school_id == school_id)
            )
        ).scalars().all()
        transfers = {row.payment_id: row for row in transfer_rows}
        out: list[OnlinePaymentRow] = []
        for payment in payments:
            if payment.school_id != school_id:
                continue
            stamp = payment.paid_at or payment.created_at
            day = stamp.date() if stamp is not None else None
            if from_date and day and day < from_date:
                continue
            if to_date and day and day > to_date:
                continue
            account = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
            student_name = None
            if account and account.school_id == school_id:
                student_name = await self.fees._student_name(account.student_id)
            out.append(
                OnlinePaymentRow(
                    payment_id=payment.id,
                    payment_number=payment.payment_number,
                    invoice_id=payment.invoice_id,
                    student_name=student_name,
                    amount=money(payment.amount),
                    currency=payment.currency,
                    status=payment.status,
                    gateway=payment.gateway,
                    gateway_order_id=payment.gateway_order_id,
                    gateway_payment_id=payment.gateway_payment_id,
                    paid_at=payment.paid_at,
                    created_at=payment.created_at,
                    payment_link=payment.notes == "payment_link",
                    transfer_status=transfers[payment.id].status if payment.id in transfers else None,
                    transfer_amount=money(transfers[payment.id].transfer_amount) if payment.id in transfers else None,
                    platform_fee_amount=(
                        money(transfers[payment.id].platform_fee_amount) if payment.id in transfers else None
                    ),
                    settlement_id=transfers[payment.id].settlement_id if payment.id in transfers else None,
                )
            )
        return out

    async def verify_payment(
        self, school_id: UUID, user_id: UUID, data: PaymentVerifyIn
    ) -> PaymentOut:
        await bind_tenant(self.db, school_id)
        payment = (
            await self.db.execute(
                select(Payment).where(Payment.id == data.payment_id).with_for_update()
            )
        ).scalar_one_or_none()
        payment = assert_same_school(payment, school_id, "Payment")
        # The row was loaded under the caller's RLS, so a parent only gets here for
        # their own child's payment. Crediting writes school-level rows (sequences,
        # receipts, ledger) that PARENT RLS rejects, so finish as SERVICE in this school.
        await bind_tenant(self.db, school_id, role="SERVICE")
        if payment.status == PaymentStatus.SUCCESS.value:
            return await self._payment_out(payment)
        if payment.gateway_order_id != data.gateway_order_id:
            raise AppError("Order mismatch", status_code=422)

        core = get_settings()
        if (core.app_env or "").lower() == "production" and payment.gateway == "mock":
            raise AppError("Mock payments are disabled in production", status_code=403)

        gateway = get_gateway(payment.gateway)
        result = await gateway.verify_payment(
            order_id=data.gateway_order_id,
            payment_id=data.gateway_payment_id,
            signature=data.signature,
            amount=money(payment.amount),
            currency=payment.currency,
        )
        if not result.success:
            payment.status = PaymentStatus.FAILED.value
            await self.db.commit()
            await self._notify_payment_failed(payment)
            raise AppError("Payment verification failed", status_code=400)

        if money(result.amount) != money(payment.amount):
            raise AppError("Verified amount mismatch", status_code=400)

        return await self._finalize_success(
            payment,
            gateway_payment_id=result.payment_id,
            reference=result.reference,
            actor_user_id=user_id,
        )

    def _event_id(self, headers: dict, raw_body: bytes | None, payload: dict) -> str:
        header_id = headers.get("x-razorpay-event-id") or headers.get("x-mock-event-id")
        if header_id:
            return str(header_id)[:128]
        digest = hashlib.sha256(raw_body or json.dumps(payload, sort_keys=True, default=str).encode())
        return digest.hexdigest()

    async def handle_webhook(
        self, provider: str, payload: dict, headers: dict, raw_body: bytes | None = None
    ) -> PaymentOut | dict | None:
        core = get_settings()
        if (core.app_env or "").lower() == "production" and provider == "mock":
            raise AppError("Mock webhooks are disabled in production", status_code=403)
        gateway = get_gateway(provider)
        result = await gateway.handle_webhook(payload=payload, headers=headers, raw_body=raw_body)
        if not result.success:
            raise AppError("Invalid webhook", status_code=400)

        event_id = self._event_id(headers, raw_body, payload)
        routed = await maybe_handle_route(self, result, event_id, payload)
        if routed is not None:
            return routed
        if not result.order_id:
            raise AppError("Invalid webhook", status_code=400)
        # Order ids are global at the gateway. Bypass RLS only for this lookup.
        await set_rls_bypass(self.db, True)
        payment = (
            await self.db.execute(
                select(Payment).where(Payment.gateway_order_id == result.order_id).with_for_update()
            )
        ).scalar_one_or_none()
        await set_rls_bypass(self.db, False)
        if not payment:
            raise NotFoundError("Payment not found for order")
        await bind_tenant(self.db, payment.school_id, role="SERVICE")

        existing_event = (
            await self.db.execute(select(FeeWebhookEvent).where(FeeWebhookEvent.event_id == event_id))
        ).scalar_one_or_none()
        if existing_event:
            if payment.status == PaymentStatus.SUCCESS.value:
                return await self._payment_out(payment)
            return {
                "ok": True,
                "credited": False,
                "duplicate": True,
                "event": result.event,
                "payment_id": str(payment.id),
            }

        credit = webhook_should_credit(result)
        if not credit:
            if payment.status == PaymentStatus.PENDING.value and is_failure_event(result.event):
                payment.status = PaymentStatus.FAILED.value
                await self._notify_payment_failed(payment)
            self._add_webhook_event(
                payment, event_id, result, credited=False, outcome="ignored", payload=payload
            )
            await self.db.commit()
            return {
                "ok": True,
                "credited": False,
                "event": result.event,
                "payment_id": str(payment.id),
                "status": payment.status,
            }

        if money(result.amount) != money(payment.amount):
            self._add_webhook_event(
                payment, event_id, result, credited=False, outcome="amount_mismatch", payload=payload
            )
            await self.db.commit()
            raise AppError("Webhook amount mismatch", status_code=400)
        if result.currency and result.currency != payment.currency:
            raise AppError("Webhook currency mismatch", status_code=400)

        self._add_webhook_event(
            payment, event_id, result, credited=True, outcome="credited", payload=payload
        )
        return await self._finalize_success(
            payment,
            gateway_payment_id=result.payment_id,
            reference=result.reference,
            actor_user_id=None,
            idempotency_key=f"webhook:{result.order_id}:{result.payment_id}",
        )

    def _add_webhook_event(
        self, payment: Payment, event_id: str, result, *, credited: bool, outcome: str, payload: dict
    ) -> None:
        self.db.add(
            FeeWebhookEvent(
                id=uuid4(),
                school_id=payment.school_id,
                provider=payment.gateway or "unknown",
                event_id=event_id,
                event_type=(result.event or "")[:64],
                order_id=result.order_id,
                gateway_payment_id=result.payment_id,
                credited=credited,
                outcome=outcome,
                payload=json.dumps(payload, default=str)[:20000],
                processed_at=_utcnow(),
                created_at=_utcnow(),
            )
        )

    async def record_manual(
        self, school_id: UUID, user_id: UUID, data: ManualPaymentIn
    ) -> PaymentOut:
        await bind_tenant(self.db, school_id)
        if data.idempotency_key:
            existing = (
                await self.db.execute(
                    select(Payment).where(
                        Payment.school_id == school_id,
                        Payment.idempotency_key == data.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return await self._payment_out(existing)
        inv = (
            await self.db.execute(
                select(FeeInvoice).where(FeeInvoice.id == data.invoice_id).with_for_update()
            )
        ).scalar_one_or_none()
        inv = assert_same_school(inv, school_id, "Invoice")
        if inv.status == FeeInvoiceStatus.CANCELLED.value:
            raise AppError("Invoice cancelled", status_code=422)
        amount = money(data.amount)
        if amount > money(inv.balance_amount):
            raise AppError("Amount exceeds balance", status_code=422)
        settings = await self.fees.get_or_create_settings(school_id)
        number = await self.fees.next_number(school_id, "PAYMENT", settings.payment_prefix)
        payment = Payment(
            id=uuid4(),
            school_id=school_id,
            student_fee_account_id=inv.student_fee_account_id,
            invoice_id=inv.id,
            payment_number=number,
            amount=amount,
            currency=inv.currency,
            method=data.method,
            status=PaymentStatus.PENDING.value,
            gateway_reference=data.reference,
            paid_at=data.paid_at or _utcnow(),
            received_by=user_id,
            notes=data.notes,
            idempotency_key=data.idempotency_key,
        )
        self.db.add(payment)
        await self.db.flush()
        return await self._finalize_success(
            payment,
            gateway_payment_id=None,
            reference=data.reference,
            actor_user_id=user_id,
            idempotency_key=data.idempotency_key,
        )

    async def _lock_payment(self, payment_id: UUID) -> Payment:
        return (
            await self.db.execute(select(Payment).where(Payment.id == payment_id).with_for_update())
        ).scalar_one()

    async def _finalize_success(
        self,
        payment: Payment,
        *,
        gateway_payment_id: str | None,
        reference: str | None,
        actor_user_id: UUID | None,
        idempotency_key: str | None = None,
        already_success: bool = False,
    ) -> PaymentOut:
        del already_success  # crediting is decided from locked row state, not the caller flag
        try:
            payment = await self._lock_payment(payment.id)
        except Exception:
            payment = payment
        allocs = (
            await self.db.execute(
                select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
            )
        ).scalars().all()
        receipt = (
            await self.db.execute(select(FeeReceipt).where(FeeReceipt.payment_id == payment.id))
        ).scalar_one_or_none()
        if payment.status == PaymentStatus.SUCCESS.value and allocs and receipt:
            await self.db.commit()
            return await self._payment_out(payment)

        if gateway_payment_id:
            other = (
                await self.db.execute(
                    select(Payment).where(
                        Payment.gateway_payment_id == gateway_payment_id,
                        Payment.id != payment.id,
                    )
                )
            ).scalar_one_or_none()
            if other and other.status == PaymentStatus.SUCCESS.value:
                return await self._payment_out(other)

        if idempotency_key:
            other = (
                await self.db.execute(
                    select(Payment).where(
                        Payment.school_id == payment.school_id,
                        Payment.idempotency_key == idempotency_key,
                        Payment.id != payment.id,
                        Payment.status == PaymentStatus.SUCCESS.value,
                    )
                )
            ).scalar_one_or_none()
            if other:
                return await self._payment_out(other)
            payment.idempotency_key = idempotency_key

        payment.status = PaymentStatus.SUCCESS.value
        payment.gateway_payment_id = gateway_payment_id or payment.gateway_payment_id
        payment.gateway_reference = reference or payment.gateway_reference
        payment.paid_at = payment.paid_at or _utcnow()

        if payment.invoice_id and not allocs:
            inv = (
                await self.db.execute(
                    select(FeeInvoice)
                    .where(
                        FeeInvoice.id == payment.invoice_id,
                        FeeInvoice.school_id == payment.school_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if inv:
                self.db.add(
                    PaymentAllocation(
                        id=uuid4(),
                        school_id=payment.school_id,
                        payment_id=payment.id,
                        invoice_id=inv.id,
                        amount=money(payment.amount),
                    )
                )
                inv.paid_amount = money(inv.paid_amount) + money(payment.amount)
                inv.balance_amount = money(inv.total_amount - inv.paid_amount)
                if inv.balance_amount < 0:
                    inv.balance_amount = money(0)
                inv.status = invoice_status_after_payment(
                    total=money(inv.total_amount),
                    paid=money(inv.paid_amount),
                    due_date=inv.due_date,
                )
                acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
                if acc and acc.school_id == payment.school_id:
                    await post_ledger_entry(
                        self.db,
                        school_id=payment.school_id,
                        student_id=acc.student_id,
                        account_id=acc.id,
                        entry_type=LedgerEntryType.PAYMENT.value,
                        debit=money(0),
                        credit=money(payment.amount),
                        ref_type="payment",
                        ref_id=payment.id,
                        narration=f"Payment {payment.payment_number}",
                        actor_user_id=actor_user_id,
                        branch_id=payment.branch_id,
                        entry_date=payment.paid_at.date(),
                    )

        if not receipt:
            settings = await self.fees.get_or_create_settings(payment.school_id)
            _fy_year, fy_label = indian_financial_year()
            rnum = await self.fees.next_number(
                payment.school_id, "RECEIPT", settings.receipt_prefix
            )
            receipt = FeeReceipt(
                id=uuid4(),
                school_id=payment.school_id,
                payment_id=payment.id,
                receipt_number=rnum,
                issued_at=_utcnow(),
                generated_by=actor_user_id,
                status=ReceiptStatus.ACTIVE.value,
                fy_label=fy_label,
                branch_id=payment.branch_id,
                reprint_count=0,
            )
            self.db.add(receipt)

        await attach_captured_transfer(self, payment)
        await self.fees._audit(
            payment.school_id,
            actor_user_id,
            "PAYMENT_RECORDED",
            "payment",
            payment.id,
            new_values={"amount": str(payment.amount), "status": payment.status},
        )
        await self._notify_payment_success(payment, receipt)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            await bind_tenant(self.db, payment.school_id)
            winner = None
            if idempotency_key:
                winner = (
                    await self.db.execute(
                        select(Payment).where(
                            Payment.school_id == payment.school_id,
                            Payment.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
            if winner is None and gateway_payment_id:
                winner = (
                    await self.db.execute(
                        select(Payment).where(Payment.gateway_payment_id == gateway_payment_id)
                    )
                ).scalar_one_or_none()
            if winner:
                return await self._payment_out(winner)
            raise
        await self.db.refresh(payment)
        return await self._payment_out(payment)

    async def _notify_payment_success(self, payment: Payment, receipt: FeeReceipt) -> None:
        acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
        if not acc:
            return
        name = await self.fees._student_name(acc.student_id)
        body = (
            f"Payment of {payment.currency} {payment.amount} received for {name}. "
            f"Receipt {receipt.receipt_number}."
        )
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == payment.school_id,
                    StudentParent.student_id == acc.student_id,
                )
            )
        ).scalars().all()
        for link in links:
            parent = await self.db.get(Parent, link.parent_id)
            if not parent:
                continue
            await create_in_app_notification(
                self.db,
                school_id=payment.school_id,
                user_id=parent.user_id,
                title="Payment received",
                body=body,
                category=NotificationType.FEE_PAYMENT_SUCCESS.value,
                link_path=f"/parent/fees/receipts",
                entity_type="payment",
                entity_id=payment.id,
                event_key=f"FEE_PAYMENT_SUCCESS:{payment.id}:{parent.user_id}",
            )
            await create_in_app_notification(
                self.db,
                school_id=payment.school_id,
                user_id=parent.user_id,
                title="Receipt ready",
                body=f"Receipt {receipt.receipt_number} is ready to download.",
                category=NotificationType.FEE_RECEIPT_READY.value,
                link_path=f"/parent/fees/receipts",
                entity_type="fee_receipt",
                entity_id=receipt.id,
                event_key=f"FEE_RECEIPT_READY:{receipt.id}:{parent.user_id}",
            )
        from src.modules.alerts.service import dispatch_alert, school_display_name

        try:
            await dispatch_alert(
                self.db,
                school_id=payment.school_id,
                event_key="payment_receipt",
                notification_type=NotificationType.FEE_RECEIPT_READY.value,
                student_id=acc.student_id,
                variables={
                    "student_name": name,
                    "amount": f"{payment.currency} {payment.amount}",
                    "receipt_number": receipt.receipt_number,
                    "paid_on": payment.created_at.strftime("%d %b %Y") if payment.created_at else "",
                    "school_name": await school_display_name(self.db, payment.school_id),
                },
                dedupe_key=f"payment_receipt:{receipt.id}",
                title="Payment received",
                link_path="/parent/fees/receipts",
            )
        except Exception:
            from src.core.telemetry import track_alert

            track_alert("fee_alert_failed", payment_id=str(payment.id))

    async def _notify_payment_failed(self, payment: Payment) -> None:
        acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
        if not acc:
            return
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == payment.school_id,
                    StudentParent.student_id == acc.student_id,
                )
            )
        ).scalars().all()
        for link in links:
            parent = await self.db.get(Parent, link.parent_id)
            if not parent:
                continue
            await create_in_app_notification(
                self.db,
                school_id=payment.school_id,
                user_id=parent.user_id,
                title="Payment failed",
                body=f"Payment {payment.payment_number} could not be verified.",
                category=NotificationType.FEE_PAYMENT_FAILED.value,
                link_path="/parent/fees",
                entity_type="payment",
                entity_id=payment.id,
                event_key=f"FEE_PAYMENT_FAILED:{payment.id}:{parent.user_id}",
            )

    def _refund_out(self, row: PaymentRefund) -> RefundOut:
        return RefundOut(
            id=row.id,
            school_id=row.school_id,
            payment_id=row.payment_id,
            amount=money(row.amount),
            reason=row.reason,
            status=row.status,
            gateway_refund_id=row.gateway_refund_id,
            created_by=row.created_by,
            approved_by=row.approved_by,
        )

    async def _open_refund_total(self, payment_id: UUID) -> Decimal:
        rows = (
            await self.db.execute(
                select(PaymentRefund).where(
                    PaymentRefund.payment_id == payment_id,
                    PaymentRefund.status.in_(
                        [
                            RefundStatus.SUCCESS.value,
                            RefundStatus.PROCESSING.value,
                            RefundStatus.PENDING_APPROVAL.value,
                        ]
                    ),
                )
            )
        ).scalars().all()
        return sum((money(r.amount) for r in rows), money(0))

    async def request_refund(
        self, school_id: UUID, user_id: UUID, payment_id: UUID, data: RefundIn, *, ip: str | None = None
    ) -> RefundOut:
        """Record a refund request. Money does not move until a different user approves."""
        await bind_tenant(self.db, school_id)
        if data.idempotency_key:
            existing = (
                await self.db.execute(
                    select(PaymentRefund).where(
                        PaymentRefund.school_id == school_id,
                        PaymentRefund.idempotency_key == data.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return self._refund_out(existing)
        payment = await self._lock_payment(payment_id)
        payment = assert_same_school(payment, school_id, "Payment")
        if payment.status not in (
            PaymentStatus.SUCCESS.value,
            PaymentStatus.PARTIALLY_REFUNDED.value,
        ):
            raise AppError("Only successful payments can be refunded", status_code=422)
        amount = money(data.amount)
        already = await self._open_refund_total(payment.id)
        if already + amount > money(payment.amount):
            raise AppError("Refund exceeds payment amount", status_code=422)
        refund = PaymentRefund(
            id=uuid4(),
            school_id=school_id,
            payment_id=payment.id,
            amount=amount,
            reason=data.reason.strip(),
            status=RefundStatus.PENDING_APPROVAL.value,
            created_by=user_id,
            idempotency_key=data.idempotency_key,
        )
        self.db.add(refund)
        self.db.add(
            FinanceApproval(
                id=uuid4(),
                school_id=school_id,
                entity_type=FinanceApprovalType.REFUND.value,
                entity_id=refund.id,
                status=ApprovalStatus.PENDING.value,
                amount=amount,
                reason=data.reason.strip(),
                requested_by=user_id,
                branch_id=payment.branch_id,
            )
        )
        await self.fees._audit(
            school_id,
            user_id,
            "REFUND_REQUESTED",
            "payment_refund",
            refund.id,
            reason=data.reason,
            old_values={"payment_status": payment.status},
            new_values={"amount": str(amount), "status": refund.status},
            ip_address=ip,
        )
        await self.db.commit()
        await self.db.refresh(refund)
        return self._refund_out(refund)

    async def refund(
        self, school_id: UUID, user_id: UUID, payment_id: UUID, data: RefundIn
    ) -> RefundOut:
        return await self.request_refund(school_id, user_id, payment_id, data)

    async def approve_refund(
        self, school_id: UUID, user_id: UUID, refund_id: UUID, *, note: str | None = None
    ) -> RefundOut:
        await bind_tenant(self.db, school_id)
        refund = (
            await self.db.execute(
                select(PaymentRefund).where(PaymentRefund.id == refund_id).with_for_update()
            )
        ).scalar_one_or_none()
        refund = assert_same_school(refund, school_id, "Refund")
        if refund.status == RefundStatus.SUCCESS.value:
            return self._refund_out(refund)
        if refund.status not in (
            RefundStatus.PENDING_APPROVAL.value,
            RefundStatus.PROCESSING.value,
        ):
            raise AppError("Refund is not awaiting approval", status_code=422)
        assert_distinct_checker(refund.created_by, user_id)
        payment = await self._lock_payment(refund.payment_id)
        assert_same_school(payment, school_id, "Payment")

        if refund.status == RefundStatus.PENDING_APPROVAL.value:
            refund.status = RefundStatus.PROCESSING.value
            await self.db.commit()

        needs_gateway = (
            payment.method == PaymentMethod.ONLINE.value
            and bool(payment.gateway)
            and bool(payment.gateway_payment_id)
            and not refund.gateway_refund_id
        )
        transfer = (
            await self.db.execute(
                select(FeeRouteTransfer).where(
                    FeeRouteTransfer.school_id == school_id,
                    FeeRouteTransfer.payment_id == payment.id,
                )
            )
        ).scalar_one_or_none()
        if needs_gateway:
            gateway = get_gateway(payment.gateway or "mock")
            try:
                result = await gateway.refund(
                    payment_id=payment.gateway_payment_id or payment.payment_number,
                    amount=money(refund.amount),
                    reverse_all=transfer is not None,
                )
            except Exception:
                await bind_tenant(self.db, school_id)
                refund = await self.db.get(PaymentRefund, refund_id)
                if refund and refund.status == RefundStatus.PROCESSING.value:
                    refund.status = RefundStatus.FAILED.value
                    await self.db.commit()
                raise
            if not result.success:
                refund = await self.db.get(PaymentRefund, refund_id)
                if refund:
                    refund.status = RefundStatus.FAILED.value
                    await self.db.commit()
                raise AppError("Gateway refund failed", status_code=400)
            refund = await self.db.get(PaymentRefund, refund_id)
            refund.gateway_refund_id = result.refund_id
            await self.db.commit()

        await bind_tenant(self.db, school_id)
        refund = (
            await self.db.execute(
                select(PaymentRefund).where(PaymentRefund.id == refund_id).with_for_update()
            )
        ).scalar_one()
        if refund.status == RefundStatus.SUCCESS.value:
            return self._refund_out(refund)
        payment = await self._lock_payment(refund.payment_id)
        applied = await reverse_payment_amount(self.db, payment, money(refund.amount))
        prior_success = (
            await self.db.execute(
                select(PaymentRefund).where(
                    PaymentRefund.payment_id == payment.id,
                    PaymentRefund.status == RefundStatus.SUCCESS.value,
                    PaymentRefund.id != refund.id,
                )
            )
        ).scalars().all()
        refunded_total = sum((money(r.amount) for r in prior_success), money(refund.amount))
        if refunded_total >= money(payment.amount):
            payment.status = PaymentStatus.REFUNDED.value
        else:
            payment.status = PaymentStatus.PARTIALLY_REFUNDED.value
        refund.status = RefundStatus.SUCCESS.value
        refund.approved_by = user_id
        refund.decision_note = note
        transfer = (
            await self.db.execute(
                select(FeeRouteTransfer).where(
                    FeeRouteTransfer.school_id == school_id,
                    FeeRouteTransfer.payment_id == payment.id,
                )
            )
        ).scalar_one_or_none()
        if transfer is not None:
            transfer.status = merge_transfer_status(transfer.status, "reversed")
        approval = (
            await self.db.execute(
                select(FinanceApproval).where(
                    FinanceApproval.school_id == school_id,
                    FinanceApproval.entity_type == FinanceApprovalType.REFUND.value,
                    FinanceApproval.entity_id == refund.id,
                )
            )
        ).scalar_one_or_none()
        if approval:
            approval.status = ApprovalStatus.APPROVED.value
            approval.approved_by = user_id
            approval.decided_at = _utcnow()
            approval.decision_note = note
        acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
        if acc and acc.school_id == school_id:
            await post_ledger_entry(
                self.db,
                school_id=school_id,
                student_id=acc.student_id,
                account_id=acc.id,
                entry_type=LedgerEntryType.REFUND.value,
                debit=applied,
                credit=money(0),
                ref_type="payment_refund",
                ref_id=refund.id,
                narration=f"Refund of {payment.payment_number}: {refund.reason[:80]}",
                actor_user_id=user_id,
                branch_id=payment.branch_id,
            )
        await self.fees._audit(
            school_id,
            user_id,
            "REFUND_APPROVED",
            "payment_refund",
            refund.id,
            reason=refund.reason,
            new_values={"amount": str(refund.amount), "applied": str(applied)},
        )
        await self.db.commit()
        await self.db.refresh(refund)
        return self._refund_out(refund)

    async def reject_refund(
        self, school_id: UUID, user_id: UUID, refund_id: UUID, *, note: str
    ) -> RefundOut:
        await bind_tenant(self.db, school_id)
        refund = (
            await self.db.execute(
                select(PaymentRefund).where(PaymentRefund.id == refund_id).with_for_update()
            )
        ).scalar_one_or_none()
        refund = assert_same_school(refund, school_id, "Refund")
        if refund.status != RefundStatus.PENDING_APPROVAL.value:
            raise AppError("Refund is not awaiting approval", status_code=422)
        assert_distinct_checker(refund.created_by, user_id)
        refund.status = RefundStatus.REJECTED.value
        refund.approved_by = user_id
        refund.decision_note = note
        approval = (
            await self.db.execute(
                select(FinanceApproval).where(
                    FinanceApproval.school_id == school_id,
                    FinanceApproval.entity_id == refund.id,
                    FinanceApproval.entity_type == FinanceApprovalType.REFUND.value,
                )
            )
        ).scalar_one_or_none()
        if approval:
            approval.status = ApprovalStatus.REJECTED.value
            approval.approved_by = user_id
            approval.decided_at = _utcnow()
            approval.decision_note = note
        await self.fees._audit(
            school_id, user_id, "REFUND_REJECTED", "payment_refund", refund.id, reason=note
        )
        await self.db.commit()
        await self.db.refresh(refund)
        return self._refund_out(refund)

    async def list_payments(
        self, school_id: UUID, *, student_id: UUID | None = None
    ) -> list[PaymentOut]:
        q = select(Payment).where(Payment.school_id == school_id).order_by(Payment.created_at.desc())
        if student_id:
            acc_ids = (
                await self.db.execute(
                    select(StudentFeeAccount.id).where(
                        StudentFeeAccount.school_id == school_id,
                        StudentFeeAccount.student_id == student_id,
                    )
                )
            ).scalars().all()
            q = q.where(Payment.student_fee_account_id.in_(acc_ids or [uuid4()]))
        rows = (await self.db.execute(q.limit(200))).scalars().all()
        return [await self._payment_out(r) for r in rows]

    async def get_receipt(self, school_id: UUID, receipt_id: UUID) -> ReceiptOut:
        r = await self.db.get(FeeReceipt, receipt_id)
        if not r or r.school_id != school_id:
            raise NotFoundError("Receipt not found")
        payment = await self.db.get(Payment, r.payment_id)
        inv = await self.db.get(FeeInvoice, payment.invoice_id) if payment and payment.invoice_id else None
        acc = (
            await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
            if payment
            else None
        )
        copy = "DUPLICATE" if (r.reprint_count or 0) > 0 else "ORIGINAL"
        if (r.status or ReceiptStatus.ACTIVE.value) != ReceiptStatus.ACTIVE.value:
            copy = f"{r.status} {copy}".strip()
        return ReceiptOut(
            id=r.id,
            school_id=r.school_id,
            payment_id=r.payment_id,
            receipt_number=r.receipt_number,
            issued_at=r.issued_at,
            amount=money(payment.amount) if payment else None,
            currency=payment.currency if payment else None,
            student_name=await self.fees._student_name(acc.student_id) if acc else None,
            invoice_number=inv.invoice_number if inv else None,
            method=payment.method if payment else None,
            status=r.status,
            copy_label=copy,
            cancel_reason=r.cancel_reason,
        )

    async def receipt_pdf_bytes(self, school_id: UUID, receipt_id: UUID) -> bytes:
        detail = await self.get_receipt(school_id, receipt_id)
        payment = await self.db.get(Payment, detail.payment_id)
        from src.db.models import School

        school = await self.db.get(School, school_id)
        from decimal import Decimal as _D

        from src.db.models import SchoolFeeProfile
        from src.modules.fee_profile.upi import gst_breakup

        profile = (
            await self.db.execute(select(SchoolFeeProfile).where(SchoolFeeProfile.school_id == school_id))
        ).scalar_one_or_none()
        gst = {}
        if profile is not None:
            parts = gst_breakup(_D(str(detail.amount or "0")), profile.gst_mode, profile.gst_rate)
            gst = {
                "gstin": profile.gstin, "sac_code": profile.sac_code, "gst_mode": parts["mode"],
                "gst_rate": str(profile.gst_rate), "taxable": str(parts["taxable"]), "cgst": str(parts["cgst"]),
                "sgst": str(parts["sgst"]), "footer": profile.receipt_footer, "state_code": profile.state_code,
            }
        return build_receipt_pdf(
            {
                **gst,
                "school_name": school.name if school else "School",
                "receipt_number": detail.receipt_number,
                "payment_number": payment.payment_number if payment else "-",
                "invoice_number": detail.invoice_number,
                "student_name": detail.student_name,
                "amount": str(detail.amount or "0"),
                "currency": detail.currency or "INR",
                "method": detail.method,
                "paid_at": str(payment.paid_at) if payment and payment.paid_at else "-",
                "status": detail.status or (payment.status if payment else "SUCCESS"),
                "copy_label": detail.copy_label or "ORIGINAL",
            }
        )

    async def assert_receipt_access(
        self, school_id: UUID, user_id: UUID, role: str, receipt_id: UUID
    ) -> None:
        if role in FINANCE_VIEW_VALUES:
            r = await self.db.get(FeeReceipt, receipt_id)
            if not r or r.school_id != school_id:
                raise NotFoundError("Receipt not found")
            return
        r = await self.db.get(FeeReceipt, receipt_id)
        if not r or r.school_id != school_id:
            raise NotFoundError("Receipt not found")
        payment = await self.db.get(Payment, r.payment_id)
        acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id) if payment else None
        if not acc:
            raise NotFoundError("Receipt not found")
        allowed = await self.fees._linked_student_ids(school_id, user_id, role)
        if acc.student_id not in allowed:
            raise ForbiddenError("Not allowed to access this receipt")
