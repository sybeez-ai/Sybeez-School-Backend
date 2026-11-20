"""Pure decisions for gateway crediting. Failed events must not create receipts."""

from __future__ import annotations

FAILURE_EVENTS = {
    "payment.failed",
    "payment.authorized",
    "payment.created",
    "refund.created",
    "refund.processed",
    "refund.failed",
    "order.notification.failed",
}


def classify_razorpay_event(payload: dict) -> tuple[bool, str, str]:
    """Return (should_credit, event_name, payment_status).

    Credit only payment.captured / order.paid when the payment is captured or paid.
    """
    event = str(payload.get("event") or "")
    payment_entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
    order_entity = ((payload.get("payload") or {}).get("order") or {}).get("entity") or {}
    status = str(payment_entity.get("status") or payload.get("status") or "").lower()
    order_status = str(order_entity.get("status") or "").lower()

    if event in FAILURE_EVENTS or status == "failed":
        return False, event or "unknown", status
    if event == "payment.captured" and status in {"captured", ""}:
        return True, event, status or "captured"
    if event == "order.paid" and status != "failed" and (
        status in {"captured", "paid", ""} or order_status == "paid"
    ):
        return True, event, status or order_status or "paid"
    if not event and status == "captured":
        return True, "payment.captured", status
    return False, event or "unknown", status


def webhook_should_credit(result) -> bool:
    """None credit means legacy gateways (mock) follow `success`."""
    credit = getattr(result, "credit", None)
    if credit is None:
        return bool(getattr(result, "success", False))
    return bool(getattr(result, "success", False) and credit)


def is_failure_event(event: str | None, status: str | None = None) -> bool:
    name = event or ""
    return name == "payment.failed" or (status or "").lower() == "failed"


def assert_distinct_checker(maker_id, checker_id) -> None:
    from src.core.exceptions import ForbiddenError

    if maker_id is not None and checker_id is not None and maker_id == checker_id:
        raise ForbiddenError("Maker cannot approve their own request")
