"""Pluggable WhatsApp senders.

meta_cloud is the default. Gupshup and Twilio are stubbed so a later adapter
can be dropped in without changing the job payload. Free text is rejected
unless the 24-hour customer service window is open. Business-initiated sends
use type=template.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


class DeliveryRejected(RuntimeError):
    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


@dataclass
class OutboundMessage:
    to: str
    body: str
    message_mode: str = "template"
    template_name: str | None = None
    language_code: str | None = None
    components: list = field(default_factory=list)
    service_window_open: bool = False


def _digits(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def meta_request_body(message: OutboundMessage) -> dict:
    to = _digits(message.to)
    mode = (message.message_mode or "template").lower()
    if mode == "text":
        if not message.service_window_open:
            raise DeliveryRejected(
                "WhatsApp free text is only allowed inside the 24-hour customer service window",
                error_code="outside_service_window",
            )
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": message.body},
        }
    if not message.template_name or not message.language_code:
        raise DeliveryRejected(
            "WhatsApp business-initiated sends require an approved template",
            error_code="template_required",
        )
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": message.template_name,
            "language": {"code": message.language_code},
            "components": message.components or [],
        },
    }


class MetaCloudProvider:
    name = "meta_cloud"

    def __init__(
        self,
        *,
        post_json: Callable[[str, dict, dict], dict],
        phone_number_id: str,
        access_token: str,
        graph_version: str = "v20.0",
    ):
        self._post = post_json
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.graph_version = graph_version

    def send(self, message: OutboundMessage) -> str:
        if not self.access_token or not self.phone_number_id:
            raise DeliveryRejected(
                "WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID missing",
                error_code="missing_credentials",
            )
        body = meta_request_body(message)
        url = f"https://graph.facebook.com/{self.graph_version}/{self.phone_number_id}/messages"
        response = self._post(
            url,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            },
            body,
        )
        messages = response.get("messages") or [{}]
        return str(messages[0].get("id") or "wa")


class GupshupProvider:
    name = "gupshup"

    def send(self, message: OutboundMessage) -> str:
        raise DeliveryRejected(
            "Gupshup WhatsApp adapter is stubbed. Set WHATSAPP_PROVIDER=meta_cloud.",
            error_code="provider_stub",
        )


class TwilioProvider:
    name = "twilio"

    def send(self, message: OutboundMessage) -> str:
        raise DeliveryRejected(
            "Twilio WhatsApp adapter is stubbed. Set WHATSAPP_PROVIDER=meta_cloud.",
            error_code="provider_stub",
        )


class MockProvider:
    name = "mock"

    def send(self, message: OutboundMessage) -> str:
        tail = _digits(message.to)[-4:] or "0000"
        return f"mock_{tail}"


def get_whatsapp_provider(name: str | None = None, **kwargs):
    """meta_cloud is the default when no provider name is given."""
    key = (name or "meta_cloud").strip().lower()
    if key in {"meta", "meta_cloud", "whatsapp_cloud"}:
        return MetaCloudProvider(
            post_json=kwargs["post_json"],
            phone_number_id=kwargs.get("phone_number_id") or "",
            access_token=kwargs.get("access_token") or "",
            graph_version=kwargs.get("graph_version") or "v20.0",
        )
    if key == "gupshup":
        return GupshupProvider()
    if key == "twilio":
        return TwilioProvider()
    if key == "mock":
        return MockProvider()
    raise DeliveryRejected(f"Unsupported WhatsApp provider: {name}", error_code="unsupported_provider")
