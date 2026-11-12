"""Onboarding steps and transfer-status merges.

Account status from Razorpay is stored as sent. Transfer status does not move
backwards from settled or reversed.
"""

from __future__ import annotations

ROUTE_STATUSES = (
    "not_started",
    "created",
    "needs_clarification",
    "under_review",
    "activated",
    "suspended",
)
TRANSFER_STATUSES = ("created", "pending", "processed", "failed", "settled", "reversed")

_STATUS_ALIASES = {
    "created": "created",
    "needs_clarification": "needs_clarification",
    "under_review": "under_review",
    "activated": "activated",
    "active": "activated",
    "suspended": "suspended",
}


class RouteStateError(ValueError):
    pass


def normalize_route_status(value: str | None) -> str | None:
    if not value:
        return None
    return _STATUS_ALIASES.get(value.strip().lower())


def status_from_event(event: str) -> str | None:
    name = (event or "").strip().lower()
    for status in ("needs_clarification", "under_review", "activated", "suspended", "created"):
        if name.endswith(status):
            return "activated" if status == "activated" else status
    return None


def assert_can_create_account(route_status: str | None, linked_account_id: str | None) -> None:
    if linked_account_id:
        raise RouteStateError("This school already has a Razorpay linked account")
    if (route_status or "not_started") not in {"not_started", ""}:
        raise RouteStateError("A linked account can only be created before onboarding starts")


def assert_can_add_stakeholder(route_status: str | None, linked_account_id: str | None) -> None:
    if not linked_account_id:
        raise RouteStateError("Create the linked account before adding a stakeholder")
    if route_status not in {"created", "needs_clarification", "under_review"}:
        raise RouteStateError(f"A stakeholder cannot be added while Route status is {route_status}")


def assert_can_configure_product(
    route_status: str | None, linked_account_id: str | None, stakeholder_id: str | None
) -> None:
    if not linked_account_id:
        raise RouteStateError("Create the linked account before requesting Route")
    if not stakeholder_id:
        raise RouteStateError("Add a stakeholder before submitting settlement details")
    if route_status not in {"created", "needs_clarification", "under_review", "activated"}:
        raise RouteStateError(f"Settlement details cannot be updated while Route status is {route_status}")


def merge_transfer_status(current: str | None, incoming: str | None) -> str:
    now = (current or "created").lower()
    nxt = (incoming or now).lower()
    if nxt not in TRANSFER_STATUSES:
        return now
    if now == "reversed":
        return now
    if now == "settled" and nxt in {"created", "pending", "processed"}:
        return now
    if now in {"processed", "failed"} and nxt in {"created", "pending"}:
        return now
    return nxt
