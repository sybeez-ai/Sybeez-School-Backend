"""Submit UTILITY templates to the WhatsApp Business Account message_templates API.

The call is optional. With no access token or business account id the result
status is "WhatsApp not configured" and nothing is posted.
"""

from __future__ import annotations

from typing import Callable

from src.modules.alerts.catalog import SAMPLE_VALUES, sanitize_param
from src.modules.alerts.credentials import WHATSAPP_NOT_CONFIGURED

GRAPH_VERSION = "v20.0"
PostJson = Callable[[str, dict, dict], dict]


def message_templates_url(waba_id: str) -> str:
    return f"https://graph.facebook.com/{GRAPH_VERSION}/{waba_id.strip()}/message_templates"


def template_payload(*, name: str, language: str, body_text: str, variables: list[str] | tuple[str, ...]) -> dict:
    example_row = [sanitize_param(SAMPLE_VALUES.get(item, "sample")) for item in variables]
    return {
        "name": name,
        "language": language,
        "category": "UTILITY",
        "allow_category_change": False,
        "components": [
            {
                "type": "BODY",
                "text": body_text,
                "example": {"body_text": [example_row]},
            }
        ],
    }


def default_post_json(url: str, headers: dict, body: dict) -> dict:
    import httpx

    response = httpx.post(url, headers=headers, json=body, timeout=30.0)
    if response.status_code >= 400:
        detail = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")
    data = response.json()
    return data if isinstance(data, dict) else {}


def submit_message_templates(
    payloads: list[dict],
    *,
    access_token: str,
    waba_id: str,
    post_json: PostJson,
    dry_run: bool = False,
) -> dict:
    token = (access_token or "").strip()
    account = (waba_id or "").strip()
    if not token or not account:
        return {
            "status": WHATSAPP_NOT_CONFIGURED,
            "dry_run": dry_run,
            "submitted": [],
            "payloads": payloads if dry_run else [],
        }
    if dry_run:
        return {
            "status": "dry_run",
            "dry_run": True,
            "submitted": [],
            "payloads": payloads,
        }
    url = message_templates_url(account)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    submitted: list[dict] = []
    for payload in payloads:
        name = str(payload.get("name") or "")
        language = str(payload.get("language") or "")
        try:
            response = post_json(url, headers, payload)
        except Exception as exc:
            submitted.append(
                {
                    "name": name,
                    "language": language,
                    "id": None,
                    "meta_status": None,
                    "error": str(exc)[:300],
                }
            )
            continue
        template_id = response.get("id")
        submitted.append(
            {
                "name": name,
                "language": language,
                "id": None if template_id is None else str(template_id),
                "meta_status": response.get("status"),
                "error": None,
            }
        )
    return {
        "status": "submitted",
        "dry_run": False,
        "submitted": submitted,
        "payloads": [],
    }
