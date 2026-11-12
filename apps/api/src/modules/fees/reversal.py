"""Apply a reversal against invoice allocations without deleting history."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.fee import FeeInvoice, Payment, PaymentAllocation
from src.modules.fees.calculations import invoice_status_after_payment, money


async def reduce_invoice_paid(inv: FeeInvoice, amount: Decimal) -> Decimal:
    applied = min(money(amount), money(inv.paid_amount))
    inv.paid_amount = money(money(inv.paid_amount) - applied)
    inv.balance_amount = money(money(inv.total_amount) - money(inv.paid_amount))
    if inv.balance_amount < 0:
        inv.balance_amount = money(0)
    inv.status = invoice_status_after_payment(
        total=money(inv.total_amount),
        paid=money(inv.paid_amount),
        due_date=inv.due_date,
    )
    return applied


async def reverse_payment_amount(
    db: AsyncSession, payment: Payment, amount: Decimal
) -> Decimal:
    """Reduce invoice paid amounts covered by this payment. Returns amount applied."""
    remaining = money(amount)
    applied_total = money(0)
    allocs = (
        await db.execute(
            select(PaymentAllocation)
            .where(
                PaymentAllocation.payment_id == payment.id,
                PaymentAllocation.school_id == payment.school_id,
            )
            .order_by(PaymentAllocation.created_at.desc())
        )
    ).scalars().all()
    invoice_ids: list[UUID] = [a.invoice_id for a in allocs]
    if payment.invoice_id and payment.invoice_id not in invoice_ids:
        invoice_ids.append(payment.invoice_id)
    for inv_id in invoice_ids:
        if remaining <= 0:
            break
        inv = (
            await db.execute(
                select(FeeInvoice)
                .where(FeeInvoice.id == inv_id, FeeInvoice.school_id == payment.school_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not inv:
            continue
        applied = await reduce_invoice_paid(inv, remaining)
        remaining = money(remaining - applied)
        applied_total = money(applied_total + applied)
    return applied_total
