"""Append-only student ledger posts. Duplicate refs are ignored."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.fee import FeeAdjustment, FeeInvoice, Payment, StudentFeeAccount
from src.db.models.finance import FeeLedgerEntry
from src.modules.fees.calculations import money
from src.shared.enums import FeeInvoiceStatus, LedgerEntryType, PaymentStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def post_ledger_entry(
    db: AsyncSession,
    *,
    school_id: UUID,
    student_id: UUID,
    account_id: UUID | None,
    entry_type: str,
    debit: Decimal,
    credit: Decimal,
    ref_type: str,
    ref_id: UUID | None,
    narration: str,
    actor_user_id: UUID | None = None,
    entry_date: date | None = None,
    branch_id: UUID | None = None,
) -> None:
    entry = FeeLedgerEntry(
        id=uuid4(),
        school_id=school_id,
        student_id=student_id,
        account_id=account_id,
        branch_id=branch_id,
        entry_date=entry_date or date.today(),
        entry_type=entry_type,
        debit=money(debit),
        credit=money(credit),
        ref_type=ref_type,
        ref_id=ref_id,
        narration=narration[:500],
        actor_user_id=actor_user_id,
        created_at=_now(),
    )
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except IntegrityError:
        return


async def ensure_ledger_backfill(db: AsyncSession, school_id: UUID, student_id: UUID) -> None:
    """Insert historical demands, concessions and payments once. Idempotent."""
    accounts = (
        await db.execute(
            select(StudentFeeAccount).where(
                StudentFeeAccount.school_id == school_id,
                StudentFeeAccount.student_id == student_id,
            )
        )
    ).scalars().all()
    for acc in accounts:
        invoices = (
            await db.execute(
                select(FeeInvoice).where(FeeInvoice.student_fee_account_id == acc.id)
            )
        ).scalars().all()
        for inv in invoices:
            if inv.status == FeeInvoiceStatus.CANCELLED.value:
                continue
            if inv.school_id != school_id:
                continue
            await post_ledger_entry(
                db,
                school_id=school_id,
                student_id=student_id,
                account_id=acc.id,
                entry_type=LedgerEntryType.DEMAND.value,
                debit=money(inv.total_amount),
                credit=money(0),
                ref_type="fee_invoice",
                ref_id=inv.id,
                narration=f"Invoice {inv.invoice_number}",
                entry_date=inv.invoice_date,
            )
        adjustments = (
            await db.execute(
                select(FeeAdjustment).where(FeeAdjustment.student_fee_account_id == acc.id)
            )
        ).scalars().all()
        for adj in adjustments:
            if adj.school_id != school_id:
                continue
            await post_ledger_entry(
                db,
                school_id=school_id,
                student_id=student_id,
                account_id=acc.id,
                entry_type=LedgerEntryType.CONCESSION.value,
                debit=money(0),
                credit=money(adj.amount),
                ref_type="fee_adjustment",
                ref_id=adj.id,
                narration=f"{adj.type}: {adj.reason[:80]}",
                entry_date=adj.created_at.date() if adj.created_at else date.today(),
            )
        payments = (
            await db.execute(
                select(Payment).where(
                    Payment.student_fee_account_id == acc.id,
                    Payment.school_id == school_id,
                    Payment.status == PaymentStatus.SUCCESS.value,
                )
            )
        ).scalars().all()
        for pay in payments:
            await post_ledger_entry(
                db,
                school_id=school_id,
                student_id=student_id,
                account_id=acc.id,
                entry_type=LedgerEntryType.PAYMENT.value,
                debit=money(0),
                credit=money(pay.amount),
                ref_type="payment",
                ref_id=pay.id,
                narration=f"Payment {pay.payment_number}",
                actor_user_id=pay.received_by,
                entry_date=(pay.paid_at or pay.created_at).date(),
                branch_id=pay.branch_id,
            )
