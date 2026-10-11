"""Payments, refunds, receipts, gateway verification."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
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
from src.modules.fees.calculations import invoice_status_after_payment, money
from src.modules.fees.gateways import get_gateway
from src.modules.fees.receipt_pdf import build_receipt_pdf
from src.modules.fees.schemas import (
    ManualPaymentIn,
    PaymentOrderIn,
    PaymentOrderOut,
    PaymentOut,
    PaymentVerifyIn,
    ReceiptOut,
    RefundIn,
)
from src.modules.fees.service import FeeService
from src.modules.notifications.service import create_in_app_notification
from src.shared.enums import (
    FeeInvoiceStatus,
    NotificationType,
    PaymentMethod,
    PaymentStatus,
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
        self, school_id: UUID, user_id: UUID, role: str, data: PaymentOrderIn
    ) -> PaymentOrderOut:
        inv = await self.db.get(FeeInvoice, data.invoice_id)
        if not inv or inv.school_id != school_id:
            raise NotFoundError("Invoice not found")
        if inv.status in (FeeInvoiceStatus.CANCELLED.value, FeeInvoiceStatus.PAID.value):
            raise AppError("Invoice is not payable", status_code=422)
        if role == UserRole.PARENT.value:
            await self._assert_parent_invoice(school_id, user_id, inv)
        elif role != UserRole.SCHOOL_ADMIN.value:
            raise ForbiddenError("Not allowed to create payment orders")

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
        order = await gateway.create_order(
            amount=amount,
            currency=inv.currency or settings.currency,
            receipt=number,
            notes={"invoice_id": str(inv.id)},
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
        )
        self.db.add(payment)
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
        )

    async def verify_payment(
        self, school_id: UUID, user_id: UUID, data: PaymentVerifyIn
    ) -> PaymentOut:
        payment = await self.db.get(Payment, data.payment_id)
        if not payment or payment.school_id != school_id:
            raise NotFoundError("Payment not found")
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

    async def handle_webhook(
        self, provider: str, payload: dict, headers: dict, raw_body: bytes | None = None
    ) -> PaymentOut | None:
        core = get_settings()
        if (core.app_env or "").lower() == "production" and provider == "mock":
            raise AppError("Mock webhooks are disabled in production", status_code=403)
        gateway = get_gateway(provider)
        result = await gateway.handle_webhook(payload=payload, headers=headers, raw_body=raw_body)
        if not result.success or not result.order_id:
            raise AppError("Invalid webhook", status_code=400)
        payment = (
            await self.db.execute(
                select(Payment).where(Payment.gateway_order_id == result.order_id)
            )
        ).scalar_one_or_none()
        if not payment:
            raise NotFoundError("Payment not found for order")
        # Idempotency
        if payment.status == PaymentStatus.SUCCESS.value:
            return await self._payment_out(payment)
        if money(result.amount) != money(payment.amount):
            raise AppError("Webhook amount mismatch", status_code=400)
        if result.currency and result.currency != payment.currency:
            raise AppError("Webhook currency mismatch", status_code=400)
        return await self._finalize_success(
            payment,
            gateway_payment_id=result.payment_id,
            reference=result.reference,
            actor_user_id=None,
            idempotency_key=f"webhook:{result.order_id}:{result.payment_id}",
        )

    async def record_manual(
        self, school_id: UUID, user_id: UUID, data: ManualPaymentIn
    ) -> PaymentOut:
        inv = await self.db.get(FeeInvoice, data.invoice_id)
        if not inv or inv.school_id != school_id:
            raise NotFoundError("Invoice not found")
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
            status=PaymentStatus.SUCCESS.value,
            gateway_reference=data.reference,
            paid_at=data.paid_at or _utcnow(),
            received_by=user_id,
            notes=data.notes,
        )
        self.db.add(payment)
        await self.db.flush()
        return await self._finalize_success(
            payment,
            gateway_payment_id=None,
            reference=data.reference,
            actor_user_id=user_id,
            already_success=True,
        )

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
        if idempotency_key:
            existing = (
                await self.db.execute(
                    select(Payment).where(
                        Payment.school_id == payment.school_id,
                        Payment.idempotency_key == idempotency_key,
                        Payment.status == PaymentStatus.SUCCESS.value,
                    )
                )
            ).scalar_one_or_none()
            if existing and existing.id != payment.id:
                return await self._payment_out(existing)
            payment.idempotency_key = idempotency_key

        if not already_success:
            payment.status = PaymentStatus.SUCCESS.value
        payment.gateway_payment_id = gateway_payment_id or payment.gateway_payment_id
        payment.gateway_reference = reference or payment.gateway_reference
        payment.paid_at = payment.paid_at or _utcnow()

        if payment.invoice_id:
            inv = await self.db.get(FeeInvoice, payment.invoice_id)
            if inv:
                # Allocate
                existing_alloc = (
                    await self.db.execute(
                        select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
                    )
                ).scalar_one_or_none()
                if not existing_alloc:
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

        receipt = (
            await self.db.execute(select(FeeReceipt).where(FeeReceipt.payment_id == payment.id))
        ).scalar_one_or_none()
        if not receipt:
            settings = await self.fees.get_or_create_settings(payment.school_id)
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
            )
            self.db.add(receipt)

        await self.fees._audit(
            payment.school_id,
            actor_user_id,
            "PAYMENT_RECORDED",
            "payment",
            payment.id,
        )
        await self._notify_payment_success(payment, receipt)
        await self.db.commit()
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

    async def refund(
        self, school_id: UUID, user_id: UUID, payment_id: UUID, data: RefundIn
    ) -> PaymentOut:
        payment = await self.db.get(Payment, payment_id)
        if not payment or payment.school_id != school_id:
            raise NotFoundError("Payment not found")
        if payment.status not in (
            PaymentStatus.SUCCESS.value,
            PaymentStatus.PARTIALLY_REFUNDED.value,
        ):
            raise AppError("Only successful payments can be refunded", status_code=422)
        amount = money(data.amount)
        prior = (
            await self.db.execute(
                select(PaymentRefund).where(
                    PaymentRefund.payment_id == payment.id,
                    PaymentRefund.status == RefundStatus.SUCCESS.value,
                )
            )
        ).scalars().all()
        already = sum((money(r.amount) for r in prior), money(0))
        if already + amount > money(payment.amount):
            raise AppError("Refund exceeds payment amount", status_code=422)

        gateway = get_gateway(payment.gateway or "mock")
        result = await gateway.refund(
            payment_id=payment.gateway_payment_id or payment.payment_number,
            amount=amount,
        )
        if not result.success:
            raise AppError("Gateway refund failed", status_code=400)

        refund = PaymentRefund(
            id=uuid4(),
            school_id=school_id,
            payment_id=payment.id,
            amount=amount,
            reason=data.reason.strip(),
            status=RefundStatus.SUCCESS.value,
            gateway_refund_id=result.refund_id,
            created_by=user_id,
        )
        self.db.add(refund)
        if already + amount >= money(payment.amount):
            payment.status = PaymentStatus.REFUNDED.value
        else:
            payment.status = PaymentStatus.PARTIALLY_REFUNDED.value

        if payment.invoice_id:
            inv = await self.db.get(FeeInvoice, payment.invoice_id)
            if inv:
                inv.paid_amount = money(max(money(inv.paid_amount) - amount, money(0)))
                inv.balance_amount = money(inv.total_amount - inv.paid_amount)
                inv.status = invoice_status_after_payment(
                    total=money(inv.total_amount),
                    paid=money(inv.paid_amount),
                    due_date=inv.due_date,
                )

        await self.fees._audit(
            school_id, user_id, "PAYMENT_REFUNDED", "payment", payment.id, reason=data.reason
        )
        await self.db.commit()
        return await self._payment_out(payment)

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
        )

    async def receipt_pdf_bytes(self, school_id: UUID, receipt_id: UUID) -> bytes:
        detail = await self.get_receipt(school_id, receipt_id)
        payment = await self.db.get(Payment, detail.payment_id)
        from src.db.models import School

        school = await self.db.get(School, school_id)
        return build_receipt_pdf(
            {
                "school_name": school.name if school else "School",
                "receipt_number": detail.receipt_number,
                "payment_number": payment.payment_number if payment else "-",
                "invoice_number": detail.invoice_number,
                "student_name": detail.student_name,
                "amount": str(detail.amount or "0"),
                "currency": detail.currency or "INR",
                "method": detail.method,
                "paid_at": str(payment.paid_at) if payment and payment.paid_at else "-",
                "status": payment.status if payment else "SUCCESS",
            }
        )

    async def assert_receipt_access(
        self, school_id: UUID, user_id: UUID, role: str, receipt_id: UUID
    ) -> None:
        if role == UserRole.SCHOOL_ADMIN.value:
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
