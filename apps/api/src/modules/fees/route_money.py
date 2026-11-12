"""Platform fee in paise. Half-up rounding so 0.5 paise becomes 1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from src.modules.fees.calculations import money

TWOPLACES = Decimal("0.01")
BLOCKED_ONLINE = (
    "Online payment isn't available for this school yet. Please pay at the school office."
)


class PlatformFeeError(ValueError):
    pass


@dataclass(frozen=True)
class FeeSplit:
    total_paise: int
    fee_paise: int
    transfer_paise: int
    fee_amount: Decimal
    transfer_amount: Decimal


def _paise_from_rupees(amount: Decimal) -> int:
    return int((money(amount) * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))


def _rupees_from_paise(paise: int) -> Decimal:
    return (Decimal(paise) / Decimal("100")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def split_platform_fee(amount: Decimal, percent: Decimal | None, fixed: Decimal | None) -> FeeSplit:
    total_paise = _paise_from_rupees(amount)
    if total_paise <= 0:
        raise PlatformFeeError("Amount must be positive")
    rate = Decimal(str(percent if percent is not None else 0))
    if rate < 0 or rate > Decimal("100"):
        raise PlatformFeeError("Platform fee percent must be between 0 and 100")
    fixed_amount = money(fixed or 0)
    if fixed_amount < 0:
        raise PlatformFeeError("Platform fee fixed amount cannot be negative")
    percent_paise = int(
        (Decimal(total_paise) * rate / Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP)
    )
    fixed_paise = _paise_from_rupees(fixed_amount)
    fee_paise = percent_paise + fixed_paise
    if fee_paise > total_paise:
        raise PlatformFeeError("Platform fee exceeds the payment amount")
    transfer_paise = total_paise - fee_paise
    return FeeSplit(
        total_paise=total_paise,
        fee_paise=fee_paise,
        transfer_paise=transfer_paise,
        fee_amount=_rupees_from_paise(fee_paise),
        transfer_amount=_rupees_from_paise(transfer_paise),
    )


def payment_route_mode(
    *,
    provider: str | None,
    route_status: str | None,
    linked_account_id: str | None,
    allow_platform_settlement: bool,
) -> str:
    """direct (non-Razorpay), transfer, platform, or blocked."""
    if (provider or "").lower() != "razorpay":
        return "direct"
    if (route_status or "") == "activated" and linked_account_id:
        return "transfer"
    if allow_platform_settlement:
        return "platform"
    return "blocked"


def plan_razorpay_transfers(settings, amount: Decimal, currency: str, notes: dict | None) -> tuple[list[dict] | None, FeeSplit | None]:
    """Return Route transfers, or None when the order settles without a transfer.

    Raises PlatformFeeError when online collection must be blocked.
    """
    mode = payment_route_mode(
        provider=getattr(settings, "gateway_provider", None),
        route_status=getattr(settings, "route_status", None),
        linked_account_id=getattr(settings, "linked_account_id", None),
        allow_platform_settlement=bool(getattr(settings, "allow_platform_settlement", False)),
    )
    if mode == "blocked":
        raise PlatformFeeError(BLOCKED_ONLINE)
    if mode != "transfer":
        return None, None
    if (currency or "INR").upper() != "INR":
        raise PlatformFeeError("Razorpay Route transfers require INR")
    split = split_platform_fee(
        amount,
        getattr(settings, "platform_fee_percent", 0),
        getattr(settings, "platform_fee_fixed", None),
    )
    if split.transfer_paise <= 0:
        raise PlatformFeeError("Platform fee leaves nothing to transfer to the school")
    active, until = hold_flags(
        settlement_on_hold=bool(getattr(settings, "settlement_on_hold", False)),
        settlement_hold_until=getattr(settings, "settlement_hold_until", None),
    )
    transfer = {
        "account": settings.linked_account_id,
        "amount": split.transfer_paise,
        "currency": "INR",
        "notes": {str(key): str(value) for key, value in (notes or {}).items() if value is not None},
        "on_hold": active,
    }
    if until is not None:
        transfer["on_hold_until"] = until
    return [transfer], split


def hold_flags(
    *,
    settlement_on_hold: bool,
    settlement_hold_until: datetime | None,
    now: datetime | None = None,
) -> tuple[bool, int | None]:
    moment = now or datetime.now(timezone.utc)
    until = settlement_hold_until
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    future = until is not None and until > moment
    active = bool(settlement_on_hold) or future
    stamp = int(until.timestamp()) if active and until is not None else None
    return active, stamp
