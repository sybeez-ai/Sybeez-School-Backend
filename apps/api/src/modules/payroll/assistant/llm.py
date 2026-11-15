"""Outbound model for the payroll assistant. Tools still run in this API."""

from __future__ import annotations

import json
import logging

from src.core.config import Settings, get_settings
from src.modules.payroll.assistant.catalog import NAV_TOOLS, READ_TOOLS, WRITE_TOOLS
from src.modules.payroll.assistant.planner import ToolCall

logger = logging.getLogger(__name__)

_SECRET: dict | None = None


def tool_specs() -> list[dict]:
    specs = []
    for name in READ_TOOLS:
        if name == "get_report":
            text = "Return a short-lived download URL for a payroll report the signed-in user is allowed to open."
        else:
            text = "Read payroll data for the signed-in school. Do not invent figures."
        specs.append(_spec(name, text))
    for name, (path, label) in NAV_TOOLS.items():
        specs.append(_spec(name, f"Ask the frontend to open {label} at {path}. This does not change data."))
    for name in WRITE_TOOLS:
        specs.append(_spec(name, "Propose this change. It does not run until the user confirms it."))
    return specs


def _spec(name: str, description: str) -> dict:
    if name == "get_report":
        properties = {
            "kind": {
                "type": "string",
                "enum": [
                    "payslip",
                    "payslips_zip",
                    "bank",
                    "pf_ecr",
                    "esi",
                    "pt",
                    "tds_24q",
                    "form16",
                    "register",
                    "audit",
                    "loans",
                    "arrears",
                    "reimbursements",
                ],
            },
            "run_id": {"type": "string"},
            "payslip_id": {"type": "string"},
            "employee_id": {"type": "string"},
            "financial_year": {"type": "string"},
            "period_start": {"type": "string"},
            "period_end": {"type": "string"},
        }
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "additionalProperties": False},
            },
        }
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
    """Return tool calls from Bedrock. None means the caller should use the local planner."""
    provider = resolved["provider"]
    if provider == "local":
        return None
    if provider in {"openai", "bedrock", "ai_service"}:
        message = await _bedrock(messages, tools=True)
        return _tool_calls(message) or []
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
        if resolved["provider"] in {"openai", "bedrock", "ai_service"}:
            message = await _bedrock(prompt, tools=False)
        else:
            return fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("payroll assistant phrase failed: %s", type(exc).__name__)
        return fallback
    text = _content(message)
    return text or fallback


def _nova_model(region: str) -> str:
    name = (region or "ap-south-1").strip()
    if name.startswith("eu-"):
        return "eu.amazon.nova-lite-v1:0"
    if name.startswith("us-"):
        return "us.amazon.nova-lite-v1:0"
    return "apac.amazon.nova-lite-v1:0"


_runtime: dict[str, object] = {}


def _runtime_client(region: str):
    cached = _runtime.get(region)
    if cached is None:
        import boto3

        cached = boto3.client("bedrock-runtime", region_name=region)
        _runtime[region] = cached
    return cached


async def _bedrock(messages: list[dict], *, tools: bool) -> dict:
    """Nova Lite through the ECS task role. No API key and no other model host."""
    import asyncio

    from botocore.exceptions import BotoCoreError, ClientError

    settings = get_settings()
    region = (settings.aws_region or "").strip() or "ap-south-1"
    converse_messages = []
    for item in _safe_messages(messages):
        text = item["content"].strip()
        if not text:
            continue
        role = "assistant" if item["role"] == "assistant" else "user"
        converse_messages.append({"role": role, "content": [{"text": text}]})
    if not converse_messages:
        converse_messages = [{"role": "user", "content": [{"text": "Hello"}]}]
    payload: dict = {
        "modelId": _nova_model(region),
        "messages": converse_messages,
        "inferenceConfig": {"maxTokens": 800, "temperature": 0.2},
    }
    if tools:
        specs = []
        for spec in tool_specs():
            function = spec.get("function") or {}
            name = str(function.get("name") or "")
            if not name:
                continue
            specs.append({
                "toolSpec": {
                    "name": name,
                    "description": str(function.get("description") or name)[:600],
                    "inputSchema": {"json": function.get("parameters") or {"type": "object", "properties": {}}},
                }
            })
        if specs:
            payload["toolConfig"] = {"tools": specs}

    def call() -> dict:
        try:
            return _runtime_client(region).converse(**payload)
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code") or type(exc).__name__)
            logger.warning("payroll bedrock failed: %s", code)
            raise ValueError("model request failed") from None
        except BotoCoreError as exc:
            logger.warning("payroll bedrock failed: %s", type(exc).__name__)
            raise ValueError("model request failed") from None

    response = await asyncio.to_thread(call)
    content = ((response.get("output") or {}).get("message") or {}).get("content") or []
    text = " ".join(str(block.get("text") or "").strip() for block in content if block.get("text")).strip()
    calls = []
    for block in content:
        tool = block.get("toolUse") or {}
        if not tool.get("name"):
            continue
        calls.append({
            "id": str(tool.get("toolUseId") or "")[:64],
            "type": "function",
            "function": {
                "name": str(tool["name"])[:64],
                "arguments": json.dumps(tool.get("input") or {}, ensure_ascii=False)[:4000],
            },
        })
    message: dict = {"content": text}
    if calls:
        message["tool_calls"] = calls
    return message


def _safe_messages(messages: list[dict]) -> list[dict]:
    return [{"role": item.get("role") or "user", "content": str(item.get("content") or "")[:4000]} for item in messages]


def _content(message: dict) -> str:
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        return " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict)).strip()
    return str(content or "").strip()


def _json_object(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def map_headers_with_model(headers: list[str], allow: list[str], resolved: dict) -> dict[str, str]:
    """Ask the configured model which unknown headers match the allow-list. Local mode maps nothing."""
    if not headers or resolved.get("provider") == "local":
        return {}
    messages = [
        {
            "role": "user",
            "content": (
                "Map these payroll column headers onto the allow-list. "
                "Reply with a JSON object whose keys are the headers and whose values are allow-list entries. "
                "Omit a header when none fits. Do not invent fields.\n"
                f"Allow-list: {', '.join(allow)}\n"
                f"Headers: {json.dumps(headers)}"
            ),
        }
    ]
    try:
        if resolved["provider"] in {"openai", "bedrock", "ai_service"}:
            message = await _bedrock(messages, tools=False)
        else:
            return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("payroll header mapping failed: %s", type(exc).__name__)
        return {}
    parsed = _json_object(_content(message))
    allowed = set(allow)
    known = set(headers)
    return {str(key): str(value) for key, value in parsed.items() if str(key) in known and str(value) in allowed}


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
