"""Per-school Route transfer, fee, and reconciliation reports."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.fee import Payment
from src.db.models.finance import FeeRouteTransfer
from src.modules.fees.calculations import money
from src.modules.fees.route_schemas import PlatformFeeReportOut, ReconciliationRowOut, TransferRowOut
from src.modules.fees.tenant import bind_tenant
from src.shared.enums import PaymentStatus


def reconcile_payment(
    *,
    payment_status: str,
    payment_amount: Decimal,
    transfer_amount: Decimal | None,
    platform_fee_amount: Decimal | None,
    transfer_status: str | None,
    allow_platform_settlement: bool,
) -> tuple[bool, str, Decimal | None]:
    gross = money(payment_amount)
    fee = money(platform_fee_amount or 0)
    expected = money(gross - fee) if transfer_amount is not None or platform_fee_amount is not None else None
    if payment_status != PaymentStatus.SUCCESS.value:
        if payment_status in {PaymentStatus.REFUNDED.value, PaymentStatus.PARTIALLY_REFUNDED.value}:
            return transfer_status == "reversed", "refunded", expected
        return False, "awaiting_capture", expected
    if transfer_status is None:
        if allow_platform_settlement:
            return True, "platform_settlement", money(0)
        return False, "missing_transfer", expected
    if transfer_status == "failed":
        return False, "transfer_failed", expected
    if transfer_status in {"created", "pending"}:
        return False, "transfer_pending", expected
    if transfer_status == "reversed":
        return True, "reversed", expected
    if expected is not None and money(transfer_amount or 0) == expected and transfer_status in {
        "processed",
        "settled",
    }:
        return True, "matched", expected
    return False, "amount_mismatch", expected


class RouteReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _rows(self, school_id: UUID, status: str | None = None) -> list[tuple[FeeRouteTransfer, Payment]]:
        await bind_tenant(self.db, school_id)
        query = (
            select(FeeRouteTransfer, Payment)
            .join(Payment, Payment.id == FeeRouteTransfer.payment_id)
            .where(FeeRouteTransfer.school_id == school_id, Payment.school_id == school_id)
            .order_by(FeeRouteTransfer.created_at.desc())
        )
        if status:
            query = query.where(FeeRouteTransfer.status == status)
        return list((await self.db.execute(query)).all())

    def _out(self, transfer: FeeRouteTransfer, payment: Payment) -> TransferRowOut:
        return TransferRowOut(
            id=transfer.id,
            school_id=transfer.school_id,
            payment_id=transfer.payment_id,
            payment_number=payment.payment_number,
            linked_account_id=transfer.linked_account_id,
            gateway_order_id=transfer.gateway_order_id,
            gateway_payment_id=transfer.gateway_payment_id,
            gateway_transfer_id=transfer.gateway_transfer_id,
            currency=transfer.currency,
            payment_amount=money(transfer.payment_amount),
            transfer_amount=money(transfer.transfer_amount),
            platform_fee_amount=money(transfer.platform_fee_amount),
            on_hold=bool(transfer.on_hold),
            status=transfer.status,
            failure_reason=transfer.failure_reason,
            settlement_id=transfer.settlement_id,
            settled_at=transfer.settled_at,
            payment_status=payment.status,
        )

    async def transfers(self, school_id: UUID, status: str | None = None) -> list[TransferRowOut]:
        return [self._out(transfer, payment) for transfer, payment in await self._rows(school_id, status)]

    async def failed_transfers(self, school_id: UUID) -> list[TransferRowOut]:
        return await self.transfers(school_id, "failed")

    async def platform_fees(self, school_id: UUID) -> PlatformFeeReportOut:
        rows = await self._rows(school_id)
        gross = money(0)
        fees = money(0)
        transferred = money(0)
        failed_amount = money(0)
        count = 0
        failed = 0
        currency = "INR"
        for transfer, payment in rows:
            currency = payment.currency or currency
            if transfer.status == "failed":
                failed += 1
                failed_amount = money(failed_amount + money(transfer.transfer_amount))
                continue
            if payment.status not in {
                PaymentStatus.SUCCESS.value,
                PaymentStatus.PARTIALLY_REFUNDED.value,
                PaymentStatus.REFUNDED.value,
            }:
                continue
            count += 1
            gross = money(gross + money(payment.amount))
            fees = money(fees + money(transfer.platform_fee_amount))
            if transfer.status in {"processed", "settled", "reversed"}:
                transferred = money(transferred + money(transfer.transfer_amount))
        return PlatformFeeReportOut(
            currency=currency,
            payment_count=count,
            gross_collected=gross,
            platform_fees=fees,
            transferred=transferred,
            failed_count=failed,
            failed_amount=failed_amount,
        )

    async def reconciliation(self, school_id: UUID, *, allow_platform_settlement: bool) -> list[ReconciliationRowOut]:
        await bind_tenant(self.db, school_id)
        payments = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.school_id == school_id,
                    Payment.gateway == "razorpay",
                )
                .order_by(Payment.created_at.desc())
            )
        ).scalars().all()
        transfer_rows = (
            await self.db.execute(
                select(FeeRouteTransfer).where(FeeRouteTransfer.school_id == school_id)
            )
        ).scalars().all()
        by_payment = {row.payment_id: row for row in transfer_rows}
        out: list[ReconciliationRowOut] = []
        for payment in payments:
            transfer = by_payment.get(payment.id)
            matched, reason, expected = reconcile_payment(
                payment_status=payment.status,
                payment_amount=money(payment.amount),
                transfer_amount=money(transfer.transfer_amount) if transfer else None,
                platform_fee_amount=money(transfer.platform_fee_amount) if transfer else None,
                transfer_status=transfer.status if transfer else None,
                allow_platform_settlement=allow_platform_settlement,
            )
            out.append(
                ReconciliationRowOut(
                    payment_id=payment.id,
                    payment_number=payment.payment_number,
                    payment_status=payment.status,
                    payment_amount=money(payment.amount),
                    platform_fee_amount=money(transfer.platform_fee_amount) if transfer else money(0),
                    transfer_amount=money(transfer.transfer_amount) if transfer else None,
                    expected_transfer=expected,
                    transfer_status=transfer.status if transfer else None,
                    settlement_id=transfer.settlement_id if transfer else None,
                    matched=matched,
                    reason=reason,
                )
            )
        return out
