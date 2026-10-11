"""Tool names the payroll assistant may call. Writes are proposals until confirm."""

from __future__ import annotations

import re
from uuid import UUID

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

READ_TOOLS = (
    "search_employee",
    "get_employee_pay",
    "get_run_status",
    "list_pending",
    "explain_payslip",
    "get_statutory_dues",
)

NAV_TOOLS = {
    "open_dashboard": ("/payroll", "Payroll home"),
    "open_runs": ("/payroll/runs", "Pay runs"),
    "open_run": ("/payroll/runs/{run_id}", "Pay run"),
    "open_employee": ("/payroll/employees/{employee_id}", "Employee pay"),
    "open_payslip": ("/payroll/payslips/{payslip_id}", "Payslip"),
    "open_settings": ("/payroll/settings", "Payroll settings"),
    "open_loans": ("/payroll/loans", "Loans"),
    "open_reports": ("/payroll/runs/{run_id}/reports", "Run reports"),
    "open_approvals": ("/payroll/approvals", "Approvals"),
}

WRITE_TOOLS = {
    "submit_run": ("run_id",),
    "approve_run": ("run_id",),
    "reject_run": ("run_id", "reason"),
    "cancel_run": ("run_id", "reason"),
    "lock_run": ("run_id",),
    "recalculate_run": ("run_id",),
    "retry_failed_payments": ("run_id", "reason"),
    "decide_reimbursement": ("item_id", "approve", "reason"),
}


def find_uuid(text: str) -> str | None:
    match = _UUID.search(text or "")
    return match.group(0) if match else None


def parse_uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def route_for(name: str, arguments: dict) -> tuple[str, str] | None:
    template = NAV_TOOLS.get(name)
    if template is None:
        return None
    path, label = template
    values = {}
    for key in re.findall(r"\{(\w+)\}", path):
        raw = (arguments or {}).get(key)
        if not raw:
            return None
        values[key] = str(raw)
    return path.format(**values), label
