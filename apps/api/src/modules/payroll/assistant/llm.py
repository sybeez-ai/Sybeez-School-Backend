"""Outbound model for the payroll assistant. Tools still run in this API."""

from __future__ import annotations

import json
import logging

import httpx

from src.core.config import Settings, get_settings
from src.modules.payroll.assistant.catalog import NAV_TOOLS, READ_TOOLS, WRITE_TOOLS
from src.modules.payroll.assistant.planner import ToolCall

logger = logging.getLogger(__name__)

_SECRET: dict | None = None


def tool_specs() -> list[dict]:
    specs = []
    for name in READ_TOOLS:
        specs.append(_spec(name, "Read payroll data for the signed-in school. Do not invent figures."))
    for name, (path, label) in NAV_TOOLS.items():
        specs.append(_spec(name, f"Ask the frontend to open {label} at {path}. This does not change data."))
    for name in WRITE_TOOLS:
        specs.append(_spec(name, "Propose this change. It does not run until the user confirms it."))
    return specs


def _spec(name: str, description: str) -> dict:
    properties: dict = {}
    if "employee" in name and name != "search_employee":
        properties["employee_id"] = {"type": "string"}
    if name == "search_employee" or name == "get_employee_pay":
        properties["query"] = {"type": "string"}
    if "run" in name or name in {"get_run_status", "open_reports"}:
        properties["run_id"] = {"type": "string"}
    if "payslip" in name:
        properties["payslip_id"] = {"type": "string"}
    if name == "decide_reimbursement":
        properties = {
            "item_id": {"type": "string"},
            "approve": {"type": "boolean"},
            "reason": {"type": "string"},
        }
    if name in {"reject_run", "cancel_run", "retry_failed_payments"}:
        properties["reason"] = {"type": "string"}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "additionalProperties": False},
        },
    }


def assistant_settings(settings: Settings | None = None) -> dict:
    """Env values, replaced by Secrets Manager when PAYROLL_ASSISTANT_SECRET_ID is set."""
    settings = settings or get_settings()
    resolved = {
        "provider": (settings.payroll_assistant_provider or "local").strip().lower(),
        "model": settings.payroll_assistant_model,
        "api_key": settings.payroll_assistant_api_key,
        "base_url": settings.payroll_assistant_base_url.rstrip("/"),
    }
    secret_id = (settings.payroll_assistant_secret_id or "").strip()
    if not secret_id:
        return resolved
    secret = _secret(secret_id, settings.s3_region or settings.cognito_region or "us-east-1")
    for key in ("provider", "model", "api_key", "base_url"):
        if secret.get(key):
            resolved[key] = str(secret[key]).strip()
    resolved["provider"] = resolved["provider"].lower()
    if resolved["base_url"]:
        resolved["base_url"] = resolved["base_url"].rstrip("/")
    return resolved


def _secret(secret_id: str, region: str) -> dict:
    global _SECRET
    if _SECRET is not None:
        return _SECRET
    import boto3

    client = boto3.client("secretsmanager", region_name=region)
    raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Payroll assistant secret must be a JSON object")
    _SECRET = parsed
    return parsed


def reset_secret_cache() -> None:
    global _SECRET
    _SECRET = None


async def plan_with_model(messages: list[dict], resolved: dict) -> list[ToolCall] | None:
    """Return tool calls from the model. None means the caller should use the local planner."""
    provider = resolved["provider"]
    if provider == "local":
        return None
    if provider == "openai":
        message = await _openai(messages, resolved, tools=True)
        return _tool_calls(message) or []
    if provider == "ai_service":
        message = await _ai_service(messages, resolved)
        calls = _tool_calls(message)
        return calls
    raise ValueError(f"Unknown payroll assistant provider {provider}")


async def phrase_with_model(messages: list[dict], outcomes: list[dict], resolved: dict, fallback: str) -> str:
    if resolved["provider"] == "local":
        return fallback
    prompt = list(messages) + [
        {
            "role": "user",
            "content": "Using only these payroll tool results, answer in a short paragraph. Do not reveal full bank numbers or PAN.\n"
            + json.dumps(outcomes, default=str)[:8000],
        }
    ]
    try:
        if resolved["provider"] == "openai":
            message = await _openai(prompt, resolved, tools=False)
        elif resolved["provider"] == "ai_service":
            message = await _ai_service(prompt, resolved)
        else:
            return fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("payroll assistant phrase failed: %s", type(exc).__name__)
        return fallback
    text = _content(message)
    return text or fallback


async def _openai(messages: list[dict], resolved: dict, *, tools: bool) -> dict:
    if not resolved.get("api_key"):
        raise ValueError("missing api key")
    body: dict = {"model": resolved["model"], "messages": _safe_messages(messages)}
    if tools:
        body["tools"] = tool_specs()
        body["tool_choice"] = "auto"
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
        response = await client.post(
            f"{resolved['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {resolved['api_key']}", "Content-Type": "application/json"},
            json=body,
        )
    if response.status_code >= 400:
        logger.warning("payroll assistant model status %s", response.status_code)
        raise ValueError("model request failed")
    data = response.json()
    return ((data.get("choices") or [{}])[0].get("message") or {})


async def _ai_service(messages: list[dict], resolved: dict) -> dict:
    """Reuse the platform AI service chat route. Tool calls are honored only when that service returns them."""
    settings = get_settings()
    url = f"{settings.ai_service_url.rstrip('/')}/api/v1/ai/chat"
    headers = {"Content-Type": "application/json"}
    token = (settings.ai_service_token or "").strip()
    if token:
        headers["X-Internal-Token"] = token
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
        response = await client.post(url, headers=headers, json={"messages": _safe_messages(messages), "tools": tool_specs()})
    if response.status_code >= 400:
        logger.warning("payroll assistant ai service status %s", response.status_code)
        raise ValueError("ai service request failed")
    data = response.json()
    if isinstance(data, dict) and "choices" in data:
        return (data.get("choices") or [{}])[0].get("message") or {}
    if isinstance(data, dict):
        text = data.get("message") or data.get("content") or data.get("summary") or ""
        return {"content": text}
    return {"content": ""}


def _safe_messages(messages: list[dict]) -> list[dict]:
    return [{"role": item.get("role") or "user", "content": str(item.get("content") or "")[:4000]} for item in messages]


def _content(message: dict) -> str:
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        return " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict)).strip()
    return str(content or "").strip()


def _tool_calls(message: dict | None) -> list[ToolCall] | None:
    if not message:
        return None
    raw = message.get("tool_calls") or []
    calls = []
    for item in raw:
        function = item.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(name, arguments))
    return calls or None
