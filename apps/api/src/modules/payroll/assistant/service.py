"""Payroll assistant. Reads and writes go through PayrollService under the caller's role and RLS."""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import Employee, PayrollAssistantAction, PayrollPayslip, PayrollPayslipLine, PayrollRun, Teacher, User
from src.modules.payroll.assistant.catalog import NAV_TOOLS, READ_TOOLS, WRITE_TOOLS, parse_uuid, route_for
from src.modules.payroll.assistant.downloads import get_report
from src.modules.payroll.assistant.llm import assistant_settings, phrase_with_model, plan_with_model
from src.modules.payroll.assistant.mask import mask_payload, redact_text
from src.modules.payroll.assistant.planner import ToolCall, plan_local, sse, summarize
from src.modules.payroll.dashboard import PayrollDashboard
from src.modules.payroll.service import VISIBLE, PayrollService

logger = logging.getLogger(__name__)


def confirm_decision(*, status: str, stored_key: str | None, incoming_key: str, key_owner_is_other: bool) -> str:
    """replay, execute, or conflict. The caller performs the side effect only for execute."""
    if key_owner_is_other:
        return "conflict"
    if status == "EXECUTED":
        return "replay" if stored_key == incoming_key else "conflict"
    if stored_key and stored_key != incoming_key:
        return "conflict"
    return "execute"


class PayrollAssistant:
    def __init__(self, payroll: PayrollService):
        self.payroll = payroll
        self.dashboard = PayrollDashboard(payroll)

    async def stream(self, messages: list[dict]):
        resolved = assistant_settings()
        redacted = redact_text(messages[-1]["content"] if messages else "")
        await self.payroll._audit("assistant.chat", "payroll_assistant", None, None, {"message": redacted[:500]}, None)
        try:
            calls = await self._plan(messages, resolved)
        except Exception as exc:  # noqa: BLE001
            logger.warning("payroll assistant plan failed: %s", type(exc).__name__)
            yield sse("error", {"message": "The payroll assistant is not available right now."})
            yield sse("done", {})
            return
        outcomes = []
        for call in calls:
            if call.name not in READ_TOOLS and call.name not in NAV_TOOLS and call.name not in WRITE_TOOLS:
                outcome = {"kind": "error", "name": call.name, "arguments": call.arguments, "message": "That action is not available."}
            else:
                yield sse("tool", {"name": call.name, "arguments": mask_payload(call.arguments)})
                outcome = await self._run(call)
            outcomes.append(outcome)
            if outcome["kind"] == "result":
                yield sse("tool_result", {"name": call.name, "result": outcome["result"]})
            elif outcome["kind"] == "navigate":
                yield sse("navigate", {"route": outcome["route"], "label": outcome["label"], "params": outcome.get("arguments") or {}})
            elif outcome["kind"] == "proposal":
                yield sse(
                    "proposal",
                    {
                        "proposal_id": outcome["proposal_id"],
                        "action": outcome["action"],
                        "summary": outcome["summary"],
                        "payload": outcome["payload"],
                    },
                )
            elif outcome["kind"] == "error":
                yield sse("error", {"message": outcome["message"]})
        fallback = summarize(outcomes)
        try:
            text = await phrase_with_model(messages, outcomes, resolved, fallback)
        except Exception:  # noqa: BLE001
            text = fallback
        for piece in _pieces(redact_text(text)):
            yield sse("token", {"text": piece})
        yield sse("done", {})

    async def confirm(self, proposal_id: UUID, idempotency_key: str) -> dict:
        row = await self.payroll.db.get(PayrollAssistantAction, proposal_id)
        if row is None or row.school_id != self.payroll.school_id or row.user_id != self.payroll.actor_id:
            raise NotFoundError("Proposal not found")
        owner = (
            await self.payroll.db.execute(
                select(PayrollAssistantAction).where(
                    PayrollAssistantAction.school_id == self.payroll.school_id,
                    PayrollAssistantAction.user_id == self.payroll.actor_id,
                    PayrollAssistantAction.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        decision = confirm_decision(
            status=row.status,
            stored_key=row.idempotency_key,
            incoming_key=idempotency_key,
            key_owner_is_other=owner is not None and owner.id != row.id,
        )
        if decision == "conflict":
            raise ConflictError("This idempotency key was already used")
        if decision == "replay":
            return {"proposal_id": str(row.id), "status": "EXECUTED", "result": row.result or {}, "replayed": True}
        result = await self._execute(row.action, row.payload or {})
        row.status = "EXECUTED"
        row.idempotency_key = idempotency_key
        row.result = result
        await self.payroll._audit(
            "assistant.confirm",
            "payroll_assistant",
            row.id,
            None,
            {"action": row.action, "result": result},
            None,
        )
        try:
            await self.payroll.db.flush()
        except IntegrityError as exc:
            raise ConflictError("This idempotency key was already used") from exc
        return {"proposal_id": str(row.id), "status": "EXECUTED", "result": result, "replayed": False}

    async def _plan(self, messages: list[dict], resolved: dict) -> list[ToolCall]:
        if resolved["provider"] == "ai_service":
            try:
                calls = await plan_with_model(messages, resolved)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ai service planner unavailable: %s", type(exc).__name__)
                calls = None
            return calls if calls is not None else plan_local(messages)
        calls = await plan_with_model(messages, resolved)
        if calls is None:
            return plan_local(messages)
        return calls

    async def _run(self, call: ToolCall) -> dict:
        try:
            if call.name in READ_TOOLS:
                result = mask_payload(await self._read(call.name, call.arguments))
                await self._tool_audit(call.name, result)
                return {"kind": "result", "name": call.name, "arguments": call.arguments, "result": result}
            if call.name in NAV_TOOLS:
                routed = route_for(call.name, call.arguments)
                if routed is None:
                    return {"kind": "error", "name": call.name, "arguments": call.arguments, "message": "I need the record id before I can open that page."}
                route, label = routed
                await self._tool_audit(call.name, {"route": route})
                return {"kind": "navigate", "name": call.name, "arguments": call.arguments, "route": route, "label": label}
            if call.name in WRITE_TOOLS:
                payload, error = _write_payload(call.name, call.arguments)
                if error:
                    return {"kind": "error", "name": call.name, "arguments": call.arguments, "message": error}
                summary = _summary(call.name, payload)
                proposal = PayrollAssistantAction(
                    id=uuid4(),
                    school_id=self.payroll.school_id,
                    user_id=self.payroll.actor_id,
                    action=call.name,
                    payload=payload,
                    summary=summary,
                    status="PROPOSED",
                )
                self.payroll.db.add(proposal)
                await self.payroll._audit("assistant.propose", "payroll_assistant", proposal.id, None, {"action": call.name, "payload": payload}, None)
                await self.payroll.db.commit()
                return {
                    "kind": "proposal",
                    "name": call.name,
                    "proposal_id": str(proposal.id),
                    "action": call.name,
                    "summary": summary,
                    "payload": payload,
                }
        except AppError as exc:
            return {"kind": "error", "name": call.name, "arguments": call.arguments, "message": exc.message}
        return {"kind": "error", "name": call.name, "arguments": call.arguments, "message": "That action is not available."}

    async def _read(self, name: str, arguments: dict) -> dict:
        if name == "search_employee":
            return await self._search(str(arguments.get("query") or ""))
        if name == "get_employee_pay":
            return await self._employee_pay(arguments)
        if name == "get_run_status":
            return await self._run_status(arguments.get("run_id"))
        if name == "list_pending":
            return await self.dashboard.pending()
        if name == "explain_payslip":
            return await self._explain(arguments.get("payslip_id"))
        if name == "get_statutory_dues":
            return await self.dashboard.statutory(None, None)
        if name == "get_report":
            return await get_report(self.payroll, arguments)
        raise AppError("Unknown read", status_code=422)

    async def _search(self, query: str) -> dict:
        await self.payroll._allow(self._read_roles())
        needle = query.replace("%", "").replace("_", "").strip()
        if len(needle) < 2:
            raise AppError("Enter at least 2 characters", status_code=422)
        pattern = f"%{needle}%"
        teacher_user = User.__table__.alias("teacher_user")
        rows = (
            await self.payroll.db.execute(
                select(Employee, User.full_name, teacher_user.c.full_name)
                .outerjoin(User, User.id == Employee.user_id)
                .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
                .outerjoin(teacher_user, teacher_user.c.id == Teacher.user_id)
                .where(
                    Employee.school_id == self.payroll.school_id,
                    or_(
                        Employee.full_name.ilike(pattern),
                        Employee.employee_code.ilike(pattern),
                        User.full_name.ilike(pattern),
                        teacher_user.c.full_name.ilike(pattern),
                    ),
                )
                .limit(20)
            )
        ).all()
        return {
            "employees": [
                {
                    "id": str(employee.id),
                    "full_name": employee.full_name or own_name or teacher_name,
                    "employee_code": employee.employee_code,
                    "staff_type": employee.staff_type,
                    "status": employee.status,
                }
                for employee, own_name, teacher_name in rows
            ]
        }

    async def _employee_pay(self, arguments: dict) -> dict:
        await self.payroll._allow(self._read_roles())
        employee = await self._resolve_employee(arguments)
        slip = (
            await self.payroll.db.execute(
                select(PayrollPayslip, PayrollRun)
                .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                .where(PayrollPayslip.employee_id == employee.id, PayrollPayslip.status != "CANCELLED")
                .order_by(PayrollRun.period_end.desc())
                .limit(1)
            )
        ).first()
        latest = None
        if slip is not None:
            payslip, run = slip
            latest = {
                "payslip_id": str(payslip.id),
                "run_id": str(run.id),
                "label": run.label,
                "approval_status": run.approval_status,
                "gross": str(payslip.gross_amount),
                "deductions": str(payslip.deductions_amount),
                "net": str(payslip.net_amount),
                "lop_days": str(payslip.lop_days or 0),
                "status": payslip.status,
            }
        return {"employee_id": str(employee.id), "full_name": employee.full_name, "latest": latest}

    async def _run_status(self, run_id) -> dict:
        parsed = parse_uuid(run_id)
        if parsed is None:
            raise AppError("A run id is required", status_code=422)
        detail = await self.payroll.get_run(parsed)
        run = detail["run"]
        return {
            "run_id": str(run.id),
            "label": run.label,
            "period_start": run.period_start.isoformat(),
            "period_end": run.period_end.isoformat(),
            "approval_status": run.approval_status,
            "payslips": len(detail["payslips"]),
        }

    async def _explain(self, payslip_id) -> dict:
        parsed = parse_uuid(payslip_id)
        if parsed is None:
            raise AppError("A payslip id is required", status_code=422)
        slip = await self.payroll.db.get(PayrollPayslip, parsed)
        if slip is None or slip.school_id != self.payroll.school_id:
            raise NotFoundError("Payslip not found")
        if not await self._can_see(slip):
            raise NotFoundError("Payslip not found")
        lines = list(
            (
                await self.payroll.db.execute(select(PayrollPayslipLine).where(PayrollPayslipLine.payslip_id == slip.id))
            ).scalars()
        )
        return {
            "payslip_id": str(slip.id),
            "status": slip.status,
            "gross": str(slip.gross_amount),
            "deductions": str(slip.deductions_amount),
            "net": str(slip.net_amount),
            "lop_days": str(slip.lop_days or 0),
            "lop_detail": slip.lop_detail or [],
            "lines": [{"code": line.code, "name": line.name, "kind": line.kind, "amount": str(line.amount)} for line in lines],
            "form16": slip.form16,
        }

    async def _execute(self, action: str, payload: dict) -> dict:
        if action == "decide_reimbursement":
            row = await self.payroll.decide_reimbursement(parse_uuid(payload["item_id"]), bool(payload["approve"]), payload["reason"])
            return {"id": str(row.id), "status": row.status}
        if action == "retry_failed_payments":
            return await self.payroll.retry_failed_payments(parse_uuid(payload["run_id"]), payload["reason"])
        if action == "recalculate_run":
            result = await self.payroll.recalculate_run(parse_uuid(payload["run_id"]), payload.get("reason"))
            return {"run_id": str(result["run"].id), "approval_status": result["run"].approval_status, "recalculated": True}
        verb = {
            "submit_run": "submit",
            "approve_run": "approve",
            "reject_run": "reject",
            "cancel_run": "cancel",
            "lock_run": "lock",
        }[action]
        run = await self.payroll.transition(parse_uuid(payload["run_id"]), verb, payload.get("reason"))
        return {"run_id": str(run.id), "approval_status": run.approval_status}

    async def _resolve_employee(self, arguments: dict) -> Employee:
        parsed = parse_uuid(arguments.get("employee_id"))
        if parsed is not None:
            return await self.payroll._employee(parsed)
        query = str(arguments.get("query") or "").strip()
        found = await self._search(query)
        people = found["employees"]
        if len(people) != 1:
            raise AppError("Name one employee so I can open their pay", status_code=422)
        return await self.payroll._employee(UUID(people[0]["id"]))

    async def _can_see(self, slip: PayrollPayslip) -> bool:
        try:
            await self.payroll._allow(self._read_roles())
            return True
        except ForbiddenError:
            employee = await self.payroll._employee_for_user()
            return slip.employee_id == employee.id and slip.status in VISIBLE

    async def _tool_audit(self, name: str, after: dict) -> None:
        await self.payroll._audit(f"assistant.tool.{name}"[:64], "payroll_assistant", None, None, after, None)

    @staticmethod
    def _read_roles():
        from src.modules.payroll.permissions import READ

        return READ


def _write_payload(name: str, arguments: dict) -> tuple[dict, str | None]:
    required = WRITE_TOOLS[name]
    payload = {}
    for key in required:
        value = arguments.get(key)
        if key in {"run_id", "item_id"}:
            if parse_uuid(value) is None:
                return {}, f"I need a valid {key.replace('_', ' ')} before I can prepare that."
            payload[key] = str(parse_uuid(value))
        elif key == "approve":
            payload[key] = bool(value)
        elif key == "reason":
            text = str(value or "").strip()
            if len(text) < 3:
                return {}, "A reason of at least 3 characters is required."
            payload[key] = text[:2000]
    if name in {"submit_run", "approve_run", "lock_run", "recalculate_run"} and arguments.get("reason"):
        payload["reason"] = str(arguments["reason"])[:2000]
    return payload, None


def _summary(name: str, payload: dict) -> str:
    target = payload.get("run_id") or payload.get("item_id") or ""
    return f"Prepare {name.replace('_', ' ')} for {target}".strip()


def _pieces(text: str) -> list[str]:
    words = (text or "").split(" ")
    pieces = []
    for index, word in enumerate(words):
        if not word and index < len(words) - 1:
            continue
        pieces.append(word if index == len(words) - 1 else word + " ")
    return [piece for piece in pieces if piece]
