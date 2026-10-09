"""Razorpay adapter stub — configure secrets via env, never expose to frontend."""

from __future__ import annotations

import hashlib
import hmac
import os
from decimal import Decimal
from uuid import uuid4

import httpx

from src.core.exceptions import AppError
from src.core.config import get_settings
from src.modules.fees.calculations import money
from src.modules.fees.gateways.base import GatewayOrder, GatewayRefundResult, GatewayVerifyResult


class RazorpayGateway:
    name = "razorpay"

    def __init__(self) -> None:
        self.key_id = os.getenv("RAZORPAY_KEY_ID", "")
        self.key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")

    async def create_order(
        self, *, amount: Decimal, currency: str, receipt: str, notes: dict | None = None
    ) -> GatewayOrder:
        settings = get_settings()
        if not self.key_id or not self.key_secret:
            # Development convenience only — never allow stub orders in production.
            if (settings.app_env or "").lower() == "production":
                raise AppError("Razorpay credentials not configured", status_code=500)
            return GatewayOrder(
                order_id=f"order_stub_{uuid4().hex[:12]}",
                amount=money(amount),
                currency=currency,
                provider=self.name,
                meta={"receipt": receipt, "mode": "stub", **(notes or {})},
            )

        # Razorpay expects amount in paise (integer).
        amount_paise = int((money(amount) * Decimal("100")).to_integral_value())
        payload = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "notes": notes or {},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.razorpay.com/v1/orders",
                    auth=(self.key_id, self.key_secret),
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise AppError(f"Razorpay order create failed: {e}", status_code=502) from e

        if resp.status_code >= 400:
            raise AppError(f"Razorpay order create failed ({resp.status_code})", status_code=502)

        data = resp.json()
        order_id = str(data.get("id") or "")
        if not order_id:
            raise AppError("Razorpay order create returned no id", status_code=502)
        return GatewayOrder(
            order_id=order_id,
            amount=money(amount),
            currency=currency,
            provider=self.name,
            meta={"receipt": receipt, "mode": "live", **(notes or {})},
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

    async def refund(self, *, payment_id: str, amount: Decimal) -> GatewayRefundResult:
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

        amount_paise = int((money(amount) * Decimal("100")).to_integral_value())
        payload = {"amount": amount_paise}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.razorpay.com/v1/payments/{payment_id}/refund",
                    auth=(self.key_id, self.key_secret),
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise AppError(f"Razorpay refund failed: {e}", status_code=502) from e

        if resp.status_code >= 400:
            raise AppError(f"Razorpay refund failed ({resp.status_code})", status_code=502)

        data = resp.json()
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
        secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
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
            )

        order_id = str(
            (payload.get("payload") or {}).get("payment", {}).get("entity", {}).get("order_id")
            or payload.get("order_id")
            or ""
        )
        payment_id = (
            (payload.get("payload") or {}).get("payment", {}).get("entity", {}).get("id")
            or payload.get("payment_id")
        )
        amount_paise = (
            (payload.get("payload") or {}).get("payment", {}).get("entity", {}).get("amount")
            or payload.get("amount")
            or 0
        )
        try:
            amount = money(Decimal(str(int(amount_paise))) / Decimal("100"))
        except Exception:
            amount = money(0)
        currency = str(
            (payload.get("payload") or {}).get("payment", {}).get("entity", {}).get("currency")
            or payload.get("currency")
            or "INR"
        )
        ok = bool(order_id)
        return GatewayVerifyResult(
            success=ok,
            order_id=order_id,
            payment_id=str(payment_id) if payment_id else None,
            amount=amount,
            currency=currency,
            reference=str(sig),
            raw=payload,
        )
