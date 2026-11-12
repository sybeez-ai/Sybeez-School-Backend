"""Route webhook side effects. Payment capture stays in PaymentService."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.db.models.fee import FeeSettings, Payment, PaymentRefund, StudentFeeAccount
from src.db.models.finance import FeeLedgerEntry, FeeRouteTransfer, FeeWebhookEvent
from src.modules.fees.calculations import money
from src.modules.fees.ledger import post_ledger_entry
from src.modules.fees.reversal import reverse_payment_amount
from src.modules.fees.route_events import describe_webhook
from src.modules.fees.route_service import apply_account_status
from src.modules.fees.route_state import merge_transfer_status
from src.modules.fees.tenant import bind_tenant, set_rls_bypass
from src.shared.enums import LedgerEntryType, PaymentStatus, RefundStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


async def maybe_handle_route(service, result, event_id: str, payload: dict) -> dict | None:
    kind = getattr(result, "kind", None)
    if kind not in {"account", "transfer", "settlement", "refund"}:
        return None
    described = describe_webhook(payload)
    if kind == "account":
        return await _account(service, described, event_id, payload)
    if kind == "transfer":
        return await _transfer(service, described, event_id, payload)
    if kind == "settlement":
        return await _settlement(service, described, event_id, payload)
    return await _refund(service, described, event_id, payload)


async def _existing(service, event_id: str) -> bool:
    row = (
        await service.db.execute(select(FeeWebhookEvent).where(FeeWebhookEvent.event_id == event_id))
    ).scalar_one_or_none()
    return row is not None


def _remember(service, school_id: UUID, event_id: str, described: dict, payload: dict, outcome: str) -> None:
    service.db.add(
        FeeWebhookEvent(
            id=uuid4(),
            school_id=school_id,
            provider="razorpay",
            event_id=event_id,
            event_type=(described.get("event") or "")[:64],
            order_id=described.get("order_id") or None,
            gateway_payment_id=described.get("payment_id"),
            credited=False,
            outcome=outcome,
            payload=json.dumps(payload, default=str)[:20000],
            processed_at=_now(),
            created_at=_now(),
        )
    )


async def _account(service, described: dict, event_id: str, payload: dict) -> dict:
    account_id = described.get("account_id")
    await set_rls_bypass(service.db, True)
    settings = None
    if account_id:
        settings = (
            await service.db.execute(select(FeeSettings).where(FeeSettings.linked_account_id == account_id))
        ).scalar_one_or_none()
    await set_rls_bypass(service.db, False)
    if settings is None:
        return {"ok": True, "matched": False, "event": described.get("event")}
    await bind_tenant(service.db, settings.school_id, role="SERVICE")
    if await _existing(service, event_id):
        return {
            "ok": True,
            "duplicate": True,
            "event": described.get("event"),
            "school_id": str(settings.school_id),
        }
    previous = apply_account_status(settings, described.get("route_status"))
    if previous is not None:
        await service.fees._audit(
            settings.school_id,
            None,
            "ROUTE_STATUS_CHANGED",
            "fee_settings",
            settings.id,
            old_values={"route_status": previous},
            new_values={"route_status": settings.route_status, "source": described.get("event")},
        )
    _remember(service, settings.school_id, event_id, described, payload, "route_status")
    await service.db.commit()
    return {
        "ok": True,
        "event": described.get("event"),
        "route_status": settings.route_status,
        "school_id": str(settings.school_id),
    }


async def _payment_for(service, described: dict) -> Payment | None:
    await set_rls_bypass(service.db, True)
    payment = None
    gateway_payment_id = described.get("payment_id")
    if gateway_payment_id:
        payment = (
            await service.db.execute(select(Payment).where(Payment.gateway_payment_id == gateway_payment_id))
        ).scalar_one_or_none()
    if payment is None and described.get("order_id"):
        payment = (
            await service.db.execute(select(Payment).where(Payment.gateway_order_id == described["order_id"]))
        ).scalar_one_or_none()
    note_payment = _uuid((described.get("notes") or {}).get("payment_id"))
    if payment is None and note_payment is not None:
        payment = await service.db.get(Payment, note_payment)
    await set_rls_bypass(service.db, False)
    return payment


async def _transfer_row(service, payment: Payment, transfer_id: str | None) -> FeeRouteTransfer | None:
    if transfer_id:
        row = (
            await service.db.execute(
                select(FeeRouteTransfer).where(
                    FeeRouteTransfer.school_id == payment.school_id,
                    FeeRouteTransfer.gateway_transfer_id == transfer_id,
                )
            )
        ).scalar_one_or_none()
        if row:
            return row
    return (
        await service.db.execute(
            select(FeeRouteTransfer).where(
                FeeRouteTransfer.school_id == payment.school_id,
                FeeRouteTransfer.payment_id == payment.id,
            )
        )
    ).scalar_one_or_none()


async def _transfer(service, described: dict, event_id: str, payload: dict) -> dict:
    payment = await _payment_for(service, described)
    if payment is None:
        return {"ok": True, "matched": False, "event": described.get("event")}
    await bind_tenant(service.db, payment.school_id, role="SERVICE")
    if await _existing(service, event_id):
        return {"ok": True, "duplicate": True, "event": described.get("event"), "payment_id": str(payment.id)}
    row = await _transfer_row(service, payment, described.get("transfer_id"))
    if row is None:
        _remember(service, payment.school_id, event_id, described, payload, "transfer_unmatched")
        await service.db.commit()
        return {"ok": True, "matched": False, "event": described.get("event"), "payment_id": str(payment.id)}
    incoming = described.get("transfer_status") or "processed"
    row.status = merge_transfer_status(row.status, incoming)
    if described.get("transfer_id"):
        row.gateway_transfer_id = described["transfer_id"]
    if described.get("payment_id"):
        row.gateway_payment_id = described["payment_id"]
    if described.get("failure_reason"):
        row.failure_reason = str(described["failure_reason"])[:500]
    if described.get("on_hold"):
        row.on_hold = True
    action = "ROUTE_TRANSFER_FAILED" if row.status == "failed" else "ROUTE_TRANSFER_PROCESSED"
    await service.fees._audit(
        payment.school_id,
        None,
        action,
        "fee_route_transfer",
        row.id,
        new_values={"status": row.status, "transfer_id": row.gateway_transfer_id},
    )
    _remember(service, payment.school_id, event_id, described, payload, row.status)
    await service.db.commit()
    return {
        "ok": True,
        "event": described.get("event"),
        "transfer_status": row.status,
        "payment_id": str(payment.id),
    }


async def _settlement(service, described: dict, event_id: str, payload: dict) -> dict:
    account_id = described.get("account_id")
    await set_rls_bypass(service.db, True)
    settings = None
    if account_id:
        settings = (
            await service.db.execute(select(FeeSettings).where(FeeSettings.linked_account_id == account_id))
        ).scalar_one_or_none()
    await set_rls_bypass(service.db, False)
    if settings is None:
        return {"ok": True, "matched": False, "event": described.get("event")}
    await bind_tenant(service.db, settings.school_id, role="SERVICE")
    if await _existing(service, event_id):
        return {"ok": True, "duplicate": True, "event": described.get("event")}
    rows = (
        await service.db.execute(
            select(FeeRouteTransfer).where(
                FeeRouteTransfer.school_id == settings.school_id,
                FeeRouteTransfer.linked_account_id == account_id,
                FeeRouteTransfer.status == "processed",
                FeeRouteTransfer.settlement_id.is_(None),
            )
        )
    ).scalars().all()
    target = money(described.get("settlement_amount") or 0)
    chosen: list[FeeRouteTransfer] = []
    exact = [row for row in rows if money(row.transfer_amount) == target]
    if len(exact) == 1:
        chosen = exact
    elif rows and money(sum((money(row.transfer_amount) for row in rows), money(0))) == target:
        chosen = list(rows)
    settlement_id = described.get("settlement_id")
    for row in chosen:
        row.status = merge_transfer_status(row.status, "settled")
        row.settlement_id = settlement_id
        row.settled_at = _now()
        await service.fees._audit(
            settings.school_id,
            None,
            "ROUTE_SETTLEMENT_RECORDED",
            "fee_route_transfer",
            row.id,
            new_values={"settlement_id": settlement_id, "status": row.status},
        )
    outcome = "settled" if chosen else "settlement_unmatched"
    _remember(service, settings.school_id, event_id, described, payload, outcome)
    await service.db.commit()
    return {
        "ok": True,
        "event": described.get("event"),
        "matched": bool(chosen),
        "count": len(chosen),
        "settlement_id": settlement_id,
    }


async def _refund(service, described: dict, event_id: str, payload: dict) -> dict:
    payment = await _payment_for(service, described)
    if payment is None:
        return {"ok": True, "matched": False, "event": described.get("event")}
    await bind_tenant(service.db, payment.school_id, role="SERVICE")
    if await _existing(service, event_id):
        return {"ok": True, "duplicate": True, "event": described.get("event"), "payment_id": str(payment.id)}
    refund = await _refund_row(service, payment, described.get("refund_id"))
    failed = described.get("event") == "refund.failed" or described.get("refund_status") == "failed"
    if refund is None:
        _remember(service, payment.school_id, event_id, described, payload, "refund_unmatched")
        await service.db.commit()
        return {"ok": True, "matched": False, "event": described.get("event"), "payment_id": str(payment.id)}
    if failed:
        if refund.status != RefundStatus.SUCCESS.value:
            refund.status = RefundStatus.FAILED.value
            await service.fees._audit(
                payment.school_id,
                None,
                "ROUTE_REFUND_FAILED",
                "payment_refund",
                refund.id,
                new_values={"status": refund.status, "gateway_refund_id": described.get("refund_id")},
            )
        _remember(service, payment.school_id, event_id, described, payload, "refund_failed")
        await service.db.commit()
        return {"ok": True, "event": described.get("event"), "refund_status": refund.status}
    if described.get("refund_id") and not refund.gateway_refund_id:
        refund.gateway_refund_id = described["refund_id"]
    if refund.status != RefundStatus.SUCCESS.value:
        await _post_refund_ledger(service, payment, refund)
    await _mark_reversed(service, payment)
    _remember(service, payment.school_id, event_id, described, payload, "refund_processed")
    try:
        await service.db.commit()
    except IntegrityError:
        await service.db.rollback()
        await bind_tenant(service.db, payment.school_id, role="SERVICE")
        return {"ok": True, "duplicate": True, "event": described.get("event"), "payment_id": str(payment.id)}
    return {
        "ok": True,
        "event": described.get("event"),
        "refund_status": refund.status,
        "payment_id": str(payment.id),
    }


async def _refund_row(service, payment: Payment, gateway_refund_id: str | None) -> PaymentRefund | None:
    if gateway_refund_id:
        row = (
            await service.db.execute(
                select(PaymentRefund).where(
                    PaymentRefund.school_id == payment.school_id,
                    PaymentRefund.gateway_refund_id == gateway_refund_id,
                )
            )
        ).scalar_one_or_none()
        if row:
            return row
    return (
        await service.db.execute(
            select(PaymentRefund)
            .where(
                PaymentRefund.school_id == payment.school_id,
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(
                    [
                        RefundStatus.PROCESSING.value,
                        RefundStatus.PENDING_APPROVAL.value,
                        RefundStatus.SUCCESS.value,
                    ]
                ),
            )
            .order_by(PaymentRefund.created_at.desc())
        )
    ).scalars().first()


async def _post_refund_ledger(service, payment: Payment, refund: PaymentRefund) -> None:
    existing = (
        await service.db.execute(
            select(FeeLedgerEntry).where(
                FeeLedgerEntry.school_id == payment.school_id,
                FeeLedgerEntry.entry_type == LedgerEntryType.REFUND.value,
                FeeLedgerEntry.ref_type == "payment_refund",
                FeeLedgerEntry.ref_id == refund.id,
            )
        )
    ).scalar_one_or_none()
    applied = money(0)
    if existing is None:
        applied = await reverse_payment_amount(service.db, payment, money(refund.amount))
        account = await service.db.get(StudentFeeAccount, payment.student_fee_account_id)
        if account and account.school_id == payment.school_id:
            await post_ledger_entry(
                service.db,
                school_id=payment.school_id,
                student_id=account.student_id,
                account_id=account.id,
                entry_type=LedgerEntryType.REFUND.value,
                debit=applied,
                credit=money(0),
                ref_type="payment_refund",
                ref_id=refund.id,
                narration=f"Refund of {payment.payment_number}",
                branch_id=payment.branch_id,
            )
    prior = (
        await service.db.execute(
            select(PaymentRefund).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status == RefundStatus.SUCCESS.value,
                PaymentRefund.id != refund.id,
            )
        )
    ).scalars().all()
    total = sum((money(row.amount) for row in prior), money(refund.amount))
    if total >= money(payment.amount):
        payment.status = PaymentStatus.REFUNDED.value
    else:
        payment.status = PaymentStatus.PARTIALLY_REFUNDED.value
    refund.status = RefundStatus.SUCCESS.value
    await service.fees._audit(
        payment.school_id,
        None,
        "ROUTE_REFUND_REVERSED",
        "payment_refund",
        refund.id,
        new_values={"amount": str(refund.amount), "applied": str(applied)},
    )


async def _mark_reversed(service, payment: Payment) -> None:
    row = (
        await service.db.execute(
            select(FeeRouteTransfer).where(
                FeeRouteTransfer.school_id == payment.school_id,
                FeeRouteTransfer.payment_id == payment.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    row.status = merge_transfer_status(row.status, "reversed")


async def attach_captured_transfer(service, payment: Payment) -> None:
    row = (
        await service.db.execute(
            select(FeeRouteTransfer).where(
                FeeRouteTransfer.school_id == payment.school_id,
                FeeRouteTransfer.payment_id == payment.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    if payment.gateway_payment_id:
        row.gateway_payment_id = payment.gateway_payment_id
    if payment.gateway_order_id:
        row.gateway_order_id = payment.gateway_order_id
