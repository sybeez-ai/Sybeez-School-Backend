"""Razorpay orders, refunds, and webhook verification.

Keys come from the environment or Secrets Manager. They are never returned
to the browser except the public key id already stored on fee settings.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

import httpx

from src.core.exceptions import AppError
from src.core.config import get_settings
from src.modules.fees.calculations import money
from src.modules.fees.gateways.base import GatewayOrder, GatewayRefundResult, GatewayVerifyResult
from src.modules.fees.gateways.razorpay_route import RazorpayRouteClient
from src.modules.fees.razorpay_secrets import razorpay_env
from src.modules.fees.route_events import describe_webhook


def _paise(amount: Decimal) -> int:
    return int((money(amount) * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))


def _string_notes(notes: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (notes or {}).items():
        if value is None:
            continue
        out[str(key)[:64]] = str(value)[:256]
    return out


class RazorpayGateway:
    name = "razorpay"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.key_id = razorpay_env("RAZORPAY_KEY_ID")
        self.key_secret = razorpay_env("RAZORPAY_KEY_SECRET")
        self._transport = transport

    def client(self) -> RazorpayRouteClient:
        self.key_id = self.key_id or razorpay_env("RAZORPAY_KEY_ID")
        self.key_secret = self.key_secret or razorpay_env("RAZORPAY_KEY_SECRET")
        return RazorpayRouteClient(self.key_id, self.key_secret, transport=self._transport)

    async def create_order(
        self,
        *,
        amount: Decimal,
        currency: str,
        receipt: str,
        notes: dict | None = None,
        transfers: list[dict] | None = None,
    ) -> GatewayOrder:
        settings = get_settings()
        string_notes = _string_notes(notes)
        if not self.key_id or not self.key_secret:
            # Development convenience only — never allow stub orders in production.
            if (settings.app_env or "").lower() == "production":
                raise AppError("Razorpay credentials not configured", status_code=500)
            return GatewayOrder(
                order_id=f"order_stub_{uuid4().hex[:12]}",
                amount=money(amount),
                currency=currency,
                provider=self.name,
                meta={"receipt": receipt, "mode": "stub", "transfers": transfers or [], **string_notes},
            )

        payload: dict = {
            "amount": _paise(amount),
            "currency": currency,
            "receipt": receipt[:40],
            "notes": string_notes,
        }
        if transfers:
            payload["transfers"] = transfers
        data = await self.client().create_order(payload)
        order_id = str(data.get("id") or "")
        if not order_id:
            raise AppError("Razorpay order create returned no id", status_code=502)
        return GatewayOrder(
            order_id=order_id,
            amount=money(amount),
            currency=currency,
            provider=self.name,
            meta={"receipt": receipt, "mode": "live", "transfers": transfers or [], **string_notes},
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
        if not payment_id or not signature:
            return GatewayVerifyResult(
                success=False,
                order_id=order_id,
                payment_id=payment_id,
                amount=money(amount),
                currency=currency,
            )
        if self.key_secret:
            expected = hmac.new(
                self.key_secret.encode(),
                f"{order_id}|{payment_id}".encode(),
                hashlib.sha256,
            ).hexdigest()
            ok = hmac.compare_digest(expected, signature)
        else:
            # Stub mode: accept only stub orders with stub signature
            ok = order_id.startswith("order_stub_") and signature == "stub_ok"
        return GatewayVerifyResult(
            success=ok,
            order_id=order_id,
            payment_id=payment_id,
            amount=money(amount),
            currency=currency,
            reference=signature,
        )

    async def refund(
        self, *, payment_id: str, amount: Decimal, reverse_all: bool = False
    ) -> GatewayRefundResult:
        settings = get_settings()
        if not self.key_id or not self.key_secret:
            # Development convenience only — never allow stub refunds in production.
            if (settings.app_env or "").lower() == "production":
                raise AppError("Razorpay credentials not configured", status_code=500)
            return GatewayRefundResult(
                success=True,
                refund_id=f"rfnd_stub_{uuid4().hex[:8]}",
                amount=money(amount),
            )

        payload: dict = {"amount": _paise(amount)}
        if reverse_all:
            payload["reverse_all"] = True
        data = await self.client().refund(payment_id, payload)
        refund_id = str(data.get("id") or "")
        if not refund_id:
            raise AppError("Razorpay refund returned no id", status_code=502)
        return GatewayRefundResult(
            success=True,
            refund_id=refund_id,
            amount=money(amount),
        )

    async def handle_webhook(
        self, *, payload: dict, headers: dict, raw_body: bytes | None = None
    ) -> GatewayVerifyResult:
        # Verify Razorpay webhook signature with RAZORPAY_WEBHOOK_SECRET.
        secret = razorpay_env("RAZORPAY_WEBHOOK_SECRET")
        sig = headers.get("x-razorpay-signature") or headers.get("X-Razorpay-Signature")
        if not secret or not sig or raw_body is None:
            return GatewayVerifyResult(
                success=False,
                order_id="",
                payment_id=None,
                amount=money(0),
                currency="INR",
                reference=str(sig) if sig else None,
                raw=payload,
            )

        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(sig)):
            return GatewayVerifyResult(
                success=False,
                order_id="",
                payment_id=None,
                amount=money(0),
                currency="INR",
                reference=str(sig),
                raw=payload,
                credit=False,
                event=str(payload.get("event") or ""),
            )

        described = describe_webhook(payload)
        signed_ok = bool(described["order_id"]) or described["kind"] in {
            "account",
            "transfer",
            "settlement",
            "refund",
        }
        return GatewayVerifyResult(
            success=signed_ok,
            order_id=described["order_id"],
            payment_id=described["payment_id"],
            amount=described["amount"],
            currency=described["currency"],
            reference=str(sig),
            raw=payload,
            credit=described["credit"],
            event=described["event"],
            kind=described["kind"],
            account_id=described["account_id"],
            route_status=described["route_status"],
            transfer_id=described["transfer_id"],
            transfer_status=described["transfer_status"],
            settlement_id=described["settlement_id"],
            refund_id=described["refund_id"],
            failure_reason=described["failure_reason"],
        )
