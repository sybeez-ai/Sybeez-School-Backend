"""Pick read-only tools. The model may suggest a tool; this module drops anything else."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

READ_TOOLS = (
    "attendance",
    "homework",
    "timetable",
    "results",
    "fees_due",
    "fee_receipts",
    "announcements",
    "payslips",
    "leave_balance",
    "lesson_material",
)
WRITE_TOOLS = ("leave_request",)

_INTENTS = (
    ("fee_receipts", re.compile(r"receipt", re.I)),
    ("fees_due", re.compile(r"\bfees?\b|dues|invoice", re.I)),
    ("leave_request", re.compile(r"(apply|request|book).{0,24}leave|leave request", re.I)),
    ("leave_balance", re.compile(r"leave balance|leaves? left|how many leave", re.I)),
    ("payslips", re.compile(r"payslip|salary|net pay", re.I)),
    ("attendance", re.compile(r"attendance|absent|present", re.I)),
    ("homework", re.compile(r"homework|assignment", re.I)),
    ("timetable", re.compile(r"timetable|time table|which period", re.I)),
    ("results", re.compile(r"\bresult|marks|score|grade", re.I)),
    ("announcements", re.compile(r"announcement|notice|circular", re.I)),
    ("lesson_material", re.compile(r"explain|doubt|lesson|chapter|taught|what is|what are", re.I)),
)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict


def allowed_tools(*, include_leave: bool) -> frozenset[str]:
    names = set(READ_TOOLS)
    if include_leave:
        names.add("leave_request")
    return frozenset(names)


def tool_specs(*, include_leave: bool) -> list[dict]:
    specs = []
    descriptions = {
        "attendance": "Recent attendance for the signed-in account only.",
        "homework": "Homework for the signed-in class only.",
        "timetable": "Timetable for the signed-in class only.",
        "results": "Published results for the signed-in student only.",
        "fees_due": "Fees still due for the signed-in student or, for finance staff, their school.",
        "fee_receipts": "Fee receipts for the signed-in student or, for finance staff, their school.",
        "announcements": "Published announcements the signed-in account is allowed to read.",
        "payslips": "Payslips. Own payslip unless the account has payroll access and names one colleague.",
        "leave_balance": "Leave balance. Own balance unless the account has HR access and names one colleague.",
        "lesson_material": "Teacher-uploaded lesson text for this class section only.",
        "leave_request": "File the signed-in staff member's own leave request. Does nothing when the school switch is off.",
    }
    for name in READ_TOOLS:
        specs.append(_spec(name, descriptions[name]))
    if include_leave:
        specs.append(_spec("leave_request", descriptions["leave_request"]))
    return specs


def _spec(name: str, description: str) -> dict:
    properties: dict = {}
    if name in {"attendance", "results", "fees_due", "fee_receipts"}:
        properties["student_id"] = {"type": "string"}
    if name in {"payslips", "leave_balance"}:
        properties["employee_name"] = {"type": "string"}
    if name == "leave_request":
        properties = {
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "reason": {"type": "string"},
            "leave_type_id": {"type": "string"},
        }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "additionalProperties": False},
        },
    }


def local_plan(text: str, persona: str, *, include_leave: bool) -> list[ToolCall]:
    allowed = allowed_tools(include_leave=include_leave)
    for name, pattern in _INTENTS:
        if name not in allowed:
            continue
        if pattern.search(text or ""):
            arguments = _leave_arguments(text) if name == "leave_request" else {}
            return [ToolCall(name, arguments)]
    if persona == "AI teacher":
        return [ToolCall("lesson_material", {})]
    return []


def _leave_arguments(text: str) -> dict:
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text or "")
    arguments: dict = {}
    if dates:
        arguments["start_date"] = dates[0]
        arguments["end_date"] = dates[1] if len(dates) > 1 else dates[0]
    reason = re.sub(r"\d{4}-\d{2}-\d{2}", "", text or "")
    reason = re.sub(r"(?i)apply|request|book|leave", "", reason).strip(" .,-")
    if len(reason) >= 3:
        arguments["reason"] = reason[:500]
    return arguments


def _calls_from_message(message: dict, allowed: frozenset[str]) -> list[ToolCall]:
    calls = []
    for item in message.get("tool_calls") or []:
        function = item.get("function") or item
        name = str(function.get("name") or item.get("name") or "")
        if name not in allowed:
            continue
        raw = function.get("arguments") if isinstance(function, dict) else item.get("arguments")
        if isinstance(raw, str):
            try:
                arguments = json.loads(raw or "{}")
            except json.JSONDecodeError:
                arguments = {}
        elif isinstance(raw, dict):
            arguments = raw
        else:
            arguments = {}
        calls.append(ToolCall(name, arguments))
    if calls:
        return calls
    content = message.get("content") or message.get("message") or ""
    if isinstance(content, list):
        content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    parsed = _json_object(str(content))
    name = str(parsed.get("tool") or parsed.get("name") or "")
    if name in allowed:
        arguments = parsed.get("arguments") if isinstance(parsed.get("arguments"), dict) else {}
        return [ToolCall(name, arguments)]
    return []


def _json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def plan_tools(
    messages: list[dict],
    persona: str,
    *,
    include_leave: bool,
    provider: str,
    ai_service_url: str,
    ai_service_token: str,
    post=None,
    access_token: str | None = None,
) -> list[ToolCall]:
    """Ask the AI service which tool to call. A failure falls back to the local keyword plan."""
    text = str(messages[-1].get("content") or "") if messages else ""
    local = local_plan(text, persona, include_leave=include_leave)
    if (provider or "local").strip().lower() != "ai_service":
        return local
    try:
        message = await _ai_service(
            messages,
            persona,
            include_leave=include_leave,
            ai_service_url=ai_service_url,
            ai_service_token=ai_service_token,
            post=post,
            access_token=access_token,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp assistant planner unavailable: %s", type(exc).__name__)
        return local
    calls = _calls_from_message(message, allowed_tools(include_leave=include_leave))
    return calls or local


async def phrase_reply(
    summary: str,
    *,
    provider: str,
    persona: str,
    ai_service_url: str,
    ai_service_token: str,
    post=None,
    access_token: str | None = None,
) -> str:
    """Ask the AI service to word the summary. The summary stays the answer when the service is down."""
    if (provider or "local").strip().lower() != "ai_service":
        return summary
    messages = [
        {
            "role": "user",
            "content": (
                f"You are the Sybeez {persona}. Rewrite the summary below for WhatsApp. "
                "Do not add names, numbers, dates, or facts that are not in the summary. "
                "If the summary says the information is missing, say that.\n\n"
                + summary[:4000]
            ),
        }
    ]
    try:
        message = await _ai_service(
            messages,
            persona,
            include_leave=False,
            ai_service_url=ai_service_url,
            ai_service_token=ai_service_token,
            post=post,
            access_token=access_token,
            tools=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp assistant phrase unavailable: %s", type(exc).__name__)
        return summary
    text = str(message.get("content") or message.get("message") or "").strip()
    return text or summary


def _chat_messages(messages: list[dict]) -> list[dict]:
    """The AI service step endpoint accepts user, assistant, and tool turns only."""
    allowed = {"user", "assistant", "tool"}
    cleaned = []
    for message in messages:
        role = str(message.get("role") or "")
        if role not in allowed:
            continue
        content = message.get("content")
        item = {"role": role, "content": (content if isinstance(content, str) else str(content or ""))[:6000]}
        if role == "tool":
            item["tool_call_id"] = str(message.get("tool_call_id") or "")[:64]
        cleaned.append(item)
    return cleaned[-12:] or [{"role": "user", "content": "hello"}]


def _declarations(*, include_leave: bool) -> list[dict]:
    declared = []
    for spec in tool_specs(include_leave=include_leave):
        function = spec.get("function") or {}
        declared.append(
            {
                "name": function.get("name"),
                "description": function.get("description"),
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return declared


async def _ai_service(
    messages: list[dict],
    persona: str,
    *,
    include_leave: bool,
    ai_service_url: str,
    ai_service_token: str,
    post=None,
    access_token: str | None = None,
    tools: bool = True,
) -> dict:
    """Call the AI service with the body it actually accepts.

    /api/v1/ai/chat needs {"message": "..."}. Tool choice is /api/v1/ai/voice/agent/step.
    Both require the signed-in user's access token. An internal token is not a session,
    and sending {"messages": ...} to /chat is what raised ValueError.
    """
    base = ai_service_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif not ai_service_token and post is None:
        raise ValueError("ai service request failed")
    if tools:
        url = f"{base}/api/v1/ai/voice/agent/step"
        body = {
            "messages": _chat_messages(messages),
            "tools": _declarations(include_leave=include_leave),
            "portal": "student" if persona == "AI teacher" else "school",
            "page": "whatsapp",
        }
    else:
        url = f"{base}/api/v1/ai/chat"
        text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                text = str(message.get("content") or "")
                break
        body = {"message": text[:4000] or "hello"}
    if post is not None:
        data = await post(url, headers, body)
    else:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
            response = await client.post(url, headers=headers, json=body)
        if response.status_code >= 400:
            raise ValueError("ai service request failed")
        data = response.json()
    if not isinstance(data, dict):
        return {}
    if tools:
        message = data.get("message")
        return message if isinstance(message, dict) else data
    reply = data.get("reply") or data.get("content") or data.get("message")
    if isinstance(reply, str):
        return {"content": reply}
    return data if data.get("content") else {}


def _system(persona: str) -> str:
    if persona == "AI teacher":
        return (
            "You are the Sybeez AI teacher for one student. Answer only with the lesson material "
            "and the tool results for that student's own class. Do not invent marks, attendance, or lessons."
        )
    return (
        "You are the Sybeez AI assistant. Use only the tool results for this signed-in person. "
        "Do not invent records and do not ask for another person's data."
    )


def summarize(outcomes: list[dict], persona: str) -> str:
    if not outcomes:
        if persona == "AI teacher":
            return "I don't have a lesson on that for your class yet. Ask your teacher to upload it, or ask about attendance, homework, or the timetable."
        return "I can check attendance, homework, the timetable, results, fees, announcements, your payslip, or your leave balance."
    parts = []
    for outcome in outcomes:
        if outcome.get("error"):
            parts.append(str(outcome["error"]))
            continue
        result = outcome.get("result")
        if isinstance(result, str):
            parts.append(result)
        elif isinstance(result, dict) and result.get("text"):
            parts.append(str(result["text"]))
        else:
            parts.append(json.dumps(result, default=str)[:800])
    text = "\n\n".join(part for part in parts if part).strip()
    return text[:1500] or "I couldn't find that on your account."
