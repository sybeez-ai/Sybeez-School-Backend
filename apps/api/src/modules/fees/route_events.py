"""Parse signed Razorpay webhook bodies into payment and Route events."""

from __future__ import annotations

from decimal import Decimal

from src.modules.fees.calculations import money
from src.modules.fees.crediting import classify_razorpay_event
from src.modules.fees.route_state import normalize_route_status, status_from_event


def _failure_reason(transfer: dict, refund: dict) -> str | None:
    error = transfer.get("error")
    if isinstance(error, dict) and error.get("description"):
        return str(error["description"])
    for value in (transfer.get("error_description"), refund.get("error_description")):
        if value:
            return str(value)
    return None


def _entity(payload: dict, name: str) -> dict:
    return ((payload.get("payload") or {}).get(name) or {}).get("entity") or {}


def _amount(paise) -> Decimal:
    try:
        return money(Decimal(int(paise)) / Decimal("100"))
    except (TypeError, ValueError):
        return money(0)


def describe_webhook(payload: dict) -> dict:
    event = str(payload.get("event") or "")
    payment = _entity(payload, "payment")
    order = _entity(payload, "order")
    transfer = _entity(payload, "transfer")
    settlement = _entity(payload, "settlement")
    refund = _entity(payload, "refund")
    account = _entity(payload, "account")
    product = _entity(payload, "product")

    account_id = str(
        payload.get("account_id")
        or account.get("id")
        or transfer.get("recipient")
        or ""
    ) or None
    order_id = str(payment.get("order_id") or order.get("id") or payload.get("order_id") or "")
    payment_id = payment.get("id") or refund.get("payment_id") or transfer.get("source") or payload.get("payment_id")
    amount_paise = payment.get("amount") or refund.get("amount") or transfer.get("amount") or payload.get("amount") or 0
    currency = str(payment.get("currency") or transfer.get("currency") or payload.get("currency") or "INR")
    should_credit, event_name, _status = classify_razorpay_event(payload)

    kind = "payment"
    route_status = None
    if event.startswith("account.") or event.startswith("product.route"):
        kind = "account"
        should_credit = False
        route_status = status_from_event(event) or normalize_route_status(
            str(product.get("activation_status") or account.get("status") or "")
        )
        if not account_id and product.get("account_id"):
            account_id = str(product.get("account_id"))
    elif event.startswith("transfer."):
        kind = "transfer"
        should_credit = False
        order_id = str((transfer.get("notes") or {}).get("order_id") or order_id)
    elif event.startswith("settlement."):
        kind = "settlement"
        should_credit = False
    elif event.startswith("refund."):
        kind = "refund"
        should_credit = False

    transfer_status = str(transfer.get("status") or "").lower() or None
    if event == "transfer.processed":
        transfer_status = "processed"
    elif event == "transfer.failed":
        transfer_status = "failed"

    return {
        "kind": kind,
        "event": event_name or event,
        "order_id": order_id,
        "payment_id": str(payment_id) if payment_id else None,
        "amount": _amount(amount_paise),
        "currency": currency,
        "credit": should_credit,
        "account_id": account_id,
        "route_status": route_status,
        "transfer_id": str(transfer.get("id")) if transfer.get("id") else None,
        "transfer_status": transfer_status,
        "transfer_amount": _amount(transfer.get("amount") or 0) if transfer else money(0),
        "on_hold": bool(transfer.get("on_hold")) if transfer else False,
        "settlement_id": str(settlement.get("id")) if settlement.get("id") else None,
        "settlement_amount": _amount(settlement.get("amount") or 0) if settlement else money(0),
        "refund_id": str(refund.get("id")) if refund.get("id") else None,
        "refund_status": "processed" if event == "refund.processed" else (
            "failed" if event == "refund.failed" else (str(refund.get("status") or "") or None)
        ),
        "failure_reason": _failure_reason(transfer, refund),
        "notes": transfer.get("notes") if isinstance(transfer.get("notes"), dict) else {},
    }
