from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass
class GatewayOrder:
    order_id: str
    amount: Decimal
    currency: str
    provider: str
    meta: dict | None = None


@dataclass
class GatewayVerifyResult:
    success: bool
    order_id: str
    payment_id: str | None
    amount: Decimal
    currency: str
    reference: str | None = None
    raw: dict | None = None
    # None → follow `success` (mock). False → signature may be valid but do not credit.
    credit: bool | None = None
    event: str | None = None


@dataclass
class GatewayRefundResult:
    success: bool
    refund_id: str | None
    amount: Decimal


class PaymentGatewayAdapter(Protocol):
    name: str

    async def create_order(
        self, *, amount: Decimal, currency: str, receipt: str, notes: dict | None = None
    ) -> GatewayOrder: ...

    async def verify_payment(
        self, *, order_id: str, payment_id: str | None, signature: str | None, amount: Decimal, currency: str
    ) -> GatewayVerifyResult: ...

    async def refund(self, *, payment_id: str, amount: Decimal) -> GatewayRefundResult: ...

    async def handle_webhook(
        self, *, payload: dict, headers: dict, raw_body: bytes | None = None
    ) -> GatewayVerifyResult: ...
