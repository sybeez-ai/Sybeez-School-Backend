"""Choose payroll tools from a message. The local planner needs no outbound model."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.modules.payroll.assistant.catalog import NAV_TOOLS, WRITE_TOOLS, find_uuid

@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict


def _query_after(text: str, markers: tuple[str, ...]) -> str:
    lower = text.lower()
    for marker in markers:
        at = lower.find(marker)
        if at >= 0:
            return text[at + len(marker) :].strip(" :.-")
    return ""


def plan_local(messages: list[dict]) -> list[ToolCall]:
    text = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            text = str(message.get("content") or "")
            break
    lower = text.lower()
    found = find_uuid(text)
    if any(word in lower for word in ("pending", "waiting for approval", "approvals")):
        return [ToolCall("list_pending", {})]
    if any(word in lower for word in ("statutory", "dues", "provident fund due", "esi due")):
        return [ToolCall("get_statutory_dues", {})]
    if "explain" in lower and "payslip" in lower:
        return [ToolCall("explain_payslip", {"payslip_id": found} if found else {})]
    if "run status" in lower or "status of the run" in lower or "status of run" in lower:
        return [ToolCall("get_run_status", {"run_id": found} if found else {})]
    if any(word in lower for word in ("salary of", "pay for", "employee pay", "how much does")):
        query = _query_after(text, ("salary of", "pay for", "employee pay", "how much does"))
        return [ToolCall("get_employee_pay", {"query": query or text})]
    if lower.startswith("find ") or "search employee" in lower or lower.startswith("who is"):
        query = _query_after(text, ("find ", "search employee", "who is"))
        return [ToolCall("search_employee", {"query": query or text})]
    if any(word in lower for word in ("open ", "go to", "take me", "show me the")):
        if "setting" in lower:
            return [ToolCall("open_settings", {})]
        if "loan" in lower:
            return [ToolCall("open_loans", {})]
        if "report" in lower:
            return [ToolCall("open_reports", {"run_id": found} if found else {})]
        if "dashboard" in lower or "home" in lower:
            return [ToolCall("open_dashboard", {})]
        if "approval" in lower:
            return [ToolCall("open_approvals", {})]
        if "payslip" in lower:
            return [ToolCall("open_payslip", {"payslip_id": found} if found else {})]
        if "employee" in lower:
            return [ToolCall("open_employee", {"employee_id": found} if found else {})]
        if "run" in lower:
            return [ToolCall("open_run", {"run_id": found} if found else {})]
        return [ToolCall("open_runs", {})]
    for name in WRITE_TOOLS:
        verb = name.split("_")[0]
        if verb in lower and ("run" in lower or "reimburse" in lower or "claim" in lower or "payment" in lower):
            arguments: dict = {}
            if "run_id" in WRITE_TOOLS[name] and found:
                arguments["run_id"] = found
            if "item_id" in WRITE_TOOLS[name] and found:
                arguments["item_id"] = found
            if "approve" in WRITE_TOOLS[name]:
                arguments["approve"] = "reject" not in lower and "deny" not in lower
            if "reason" in WRITE_TOOLS[name]:
                arguments["reason"] = _reason(text) or f"{verb.title()} from the payroll assistant"
            return [ToolCall(name, arguments)]
    if "payslip" in lower and found:
        return [ToolCall("explain_payslip", {"payslip_id": found})]
    return []


def _reason(text: str) -> str | None:
    match = re.search(r"because\s+(.{3,200})", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def summarize(outcomes: list[dict]) -> str:
    proposals = [item for item in outcomes if item.get("kind") == "proposal"]
    if proposals:
        return "I prepared this for you. Confirm it before I apply the change. " + " ".join(
            item.get("summary") or item["action"] for item in proposals
        )
    navigations = [item for item in outcomes if item.get("kind") == "navigate"]
    if navigations:
        return "Opening " + navigations[0].get("label", "that page") + "."
    errors = [item for item in outcomes if item.get("kind") == "error"]
    if errors and len(errors) == len(outcomes):
        return errors[0].get("message") or "I could not do that."
    bits = []
    for item in outcomes:
        if item.get("kind") != "result":
            continue
        result = item.get("result") or {}
        if item["name"] == "search_employee":
            people = result.get("employees") or []
            bits.append(f"I found {len(people)} employee{'s' if len(people) != 1 else ''}.")
        elif item["name"] == "list_pending":
            approvals = len((result.get("approvals") or []))
            bits.append(f"{approvals} pay run{'s' if approvals != 1 else ''} waiting for approval.")
        elif item["name"] == "get_statutory_dues":
            bits.append("Here are the statutory dues for this period.")
        elif item["name"] == "get_run_status":
            bits.append(f"The run is {result.get('approval_status') or 'unavailable'}.")
        elif item["name"] == "explain_payslip":
            bits.append("Here is how that payslip was calculated.")
        elif item["name"] == "get_employee_pay":
            bits.append("Here is the latest pay I can see for that employee.")
    if bits:
        return " ".join(bits)
    return (
        "I can look up an employee, a pay run, a payslip, or statutory dues. "
        "I can also prepare a change, and I will wait for you to confirm it."
    )


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def stream_events(outcomes: list[dict], text: str):
    for item in outcomes:
        name = item.get("name") or item.get("action")
        if item.get("kind") == "result":
            yield sse("tool", {"name": name, "arguments": item.get("arguments") or {}})
            yield sse("tool_result", {"name": name, "result": item.get("result") or {}})
        elif item.get("kind") == "navigate":
            yield sse("tool", {"name": name, "arguments": item.get("arguments") or {}})
            yield sse("navigate", {"route": item["route"], "label": item.get("label"), "params": item.get("arguments") or {}})
        elif item.get("kind") == "proposal":
            yield sse("tool", {"name": name, "arguments": item.get("payload") or {}})
            yield sse(
                "proposal",
                {
                    "proposal_id": item["proposal_id"],
                    "action": item["action"],
                    "summary": item.get("summary"),
                    "payload": item.get("payload") or {},
                },
            )
        elif item.get("kind") == "error":
            yield sse("tool", {"name": name, "arguments": item.get("arguments") or {}})
            yield sse("error", {"message": item.get("message") or "I could not do that."})
    words = text.split(" ")
    for index, word in enumerate(words):
        piece = word if index == len(words) - 1 else word + " "
        if piece:
            yield sse("token", {"text": piece})
    yield sse("done", {})
