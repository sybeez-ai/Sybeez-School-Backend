"""Razorpay HTTP for orders, refunds, and Route linked accounts."""

from __future__ import annotations

from typing import Any

import httpx

from src.core.exceptions import AppError

API_BASE = "https://api.razorpay.com"


class RazorpayRouteClient:
    def __init__(
        self,
        key_id: str,
        key_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self._transport = transport

    async def request(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self.key_id or not self.key_secret:
            raise AppError("Razorpay credentials not configured", status_code=500)
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        client_kwargs: dict = {"timeout": 15.0}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        request_kwargs: dict = {"auth": (self.key_id, self.key_secret)}
        if body is not None:
            request_kwargs["json"] = body
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.request(method, url, **request_kwargs)
        except httpx.HTTPError as exc:
            raise AppError(f"Razorpay request failed: {exc}", status_code=502) from exc
        if response.status_code >= 400:
            raise AppError(f"Razorpay request failed ({response.status_code})", status_code=502)
        data = response.json()
        if not isinstance(data, dict):
            raise AppError("Razorpay returned an unexpected payload", status_code=502)
        return data

    async def create_order(self, payload: dict) -> dict:
        return await self.request("POST", "/v1/orders", payload)

    async def refund(self, payment_id: str, payload: dict) -> dict:
        return await self.request("POST", f"/v1/payments/{payment_id}/refund", payload)

    async def create_account(self, payload: dict) -> dict:
        return await self.request("POST", "/v2/accounts", payload)

    async def create_stakeholder(self, account_id: str, payload: dict) -> dict:
        return await self.request("POST", f"/v2/accounts/{account_id}/stakeholders", payload)

    async def create_product(self, account_id: str, payload: dict) -> dict:
        return await self.request("POST", f"/v2/accounts/{account_id}/products", payload)

    async def update_product(self, account_id: str, product_id: str, payload: dict) -> dict:
        return await self.request("PATCH", f"/v2/accounts/{account_id}/products/{product_id}", payload)

    async def fetch_account(self, account_id: str) -> dict:
        return await self.request("GET", f"/v2/accounts/{account_id}")

    async def fetch_product(self, account_id: str, product_id: str) -> dict:
        return await self.request("GET", f"/v2/accounts/{account_id}/products/{product_id}")


def account_payload(data: Any, reference_id: str) -> dict:
    legal: dict[str, str] = {}
    if getattr(data, "pan", None):
        legal["pan"] = data.pan
    if getattr(data, "gst", None):
        legal["gst"] = data.gst
    body: dict[str, Any] = {
        "email": data.email,
        "phone": data.phone,
        "type": "route",
        "reference_id": reference_id,
        "legal_business_name": data.legal_business_name,
        "business_type": data.business_type,
        "contact_name": data.contact_name,
        "profile": {
            "category": "education",
            "subcategory": "school",
            "addresses": {
                "registered": {
                    "street1": data.street1,
                    "street2": data.street2 or "",
                    "city": data.city,
                    "state": data.state,
                    "postal_code": data.postal_code,
                    "country": "IN",
                }
            },
        },
    }
    if legal:
        body["legal_info"] = legal
    return body


def stakeholder_payload(data: Any) -> dict:
    body: dict[str, Any] = {
        "name": data.name,
        "email": data.email,
        "addresses": {
            "residential": {
                "street": data.street,
                "city": data.city,
                "state": data.state,
                "postal_code": data.postal_code,
                "country": "IN",
            }
        },
    }
    if getattr(data, "pan", None):
        body["kyc"] = {"pan": data.pan}
    return body


def settlement_patch(data: Any) -> dict:
    return {
        "settlements": {
            "account_number": data.account_number,
            "ifsc_code": data.ifsc_code,
            "beneficiary_name": data.beneficiary_name,
        },
        "tnc_accepted": True,
    }
