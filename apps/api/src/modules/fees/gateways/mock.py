"""Mock payment gateway for TEST mode and local development."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from src.modules.fees.calculations import money
from src.modules.fees.gateways.base import GatewayOrder, GatewayRefundResult, GatewayVerifyResult


class MockGateway:
    name = "mock"

    async def create_order(
        self, *, amount: Decimal, currency: str, receipt: str, notes: dict | None = None
    ) -> GatewayOrder:
        return GatewayOrder(
            order_id=f"mock_order_{uuid4().hex[:12]}",
            amount=money(amount),
            currency=currency,
            provider=self.name,
            meta={"receipt": receipt, **(notes or {})},
        )

    async def verify_payment(
        self,
        *,
        order_id: str,
        payment_id: str | None,
        signature: str | None,
        amount: Decimal,
        currency: str,
    ) -> GatewayVerifyResult:
        # Mock never trusts client "success" alone — requires matching order_id prefix
        ok = bool(order_id and order_id.startswith("mock_order_"))
        return GatewayVerifyResult(
            success=ok,
            order_id=order_id,
            payment_id=payment_id or f"mock_pay_{uuid4().hex[:10]}",
            amount=money(amount),
            currency=currency,
            reference=signature or "mock_verified",
            raw={"provider": self.name},
        )

    async def refund(self, *, payment_id: str, amount: Decimal) -> GatewayRefundResult:
        return GatewayRefundResult(
            success=True,
            refund_id=f"mock_rfnd_{uuid4().hex[:10]}",
            amount=money(amount),
        )

    async def handle_webhook(
        self, *, payload: dict, headers: dict, raw_body: bytes | None = None
    ) -> GatewayVerifyResult:
        order_id = str(payload.get("order_id") or payload.get("gateway_order_id") or "")
        payment_id = payload.get("payment_id") or payload.get("gateway_payment_id")
        amount = money(payload.get("amount") or 0)
        currency = str(payload.get("currency") or "INR")
        # Require mock signature header for webhook authenticity
        sig = headers.get("x-mock-signature") or headers.get("X-Mock-Signature")
        ok = bool(order_id.startswith("mock_order_") and sig == "mock_secret")
        return GatewayVerifyResult(
            success=ok and amount > 0,
            order_id=order_id,
            payment_id=str(payment_id) if payment_id else None,
            amount=amount,
            currency=currency,
            reference=str(sig) if sig else None,
            raw=payload,
        )
