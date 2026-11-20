"""Books approval compatibility must retain the existing approval guard/service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.exceptions import ForbiddenError
from src.modules.finance.router import decide_approval
from src.modules.finance.schemas import ApprovalDecisionIn
from src.shared.enums import UserRole


@pytest.mark.parametrize("status", ["PENDING", "approve", "", "UNKNOWN", None])
def test_only_terminal_decisions_are_accepted(status):
    with pytest.raises(ValidationError):
        ApprovalDecisionIn(status=status)


@pytest.mark.parametrize("status,approved", [("approved", True), ("rejected", False), ("APPROVED", True), ("REJECTED", False)])
@pytest.mark.asyncio
async def test_decision_reuses_existing_school_and_maker_checker_flow(status, approved):
    approval_id = uuid4()
    result = object()
    svc = SimpleNamespace(role=UserRole.ACCOUNTANT, decide_approval=AsyncMock(return_value=result))
    body = ApprovalDecisionIn(status=status, note="Reviewed supporting documents")
    assert await decide_approval(approval_id, body, svc) is result
    svc.decide_approval.assert_awaited_once_with(approval_id, approved, body.note)


@pytest.mark.parametrize("role", [UserRole.CASHIER, UserRole.AUDITOR, UserRole.TEACHER, UserRole.STUDENT, UserRole.PARENT])
@pytest.mark.asyncio
async def test_non_approvers_cannot_call_decision_service(role):
    svc = SimpleNamespace(role=role, decide_approval=AsyncMock())
    with pytest.raises(ForbiddenError):
        await decide_approval(uuid4(), ApprovalDecisionIn(status="APPROVED"), svc)
    svc.decide_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_guard_errors_are_not_swallowed():
    svc = SimpleNamespace(role=UserRole.ACCOUNTANT, decide_approval=AsyncMock(side_effect=ForbiddenError("Maker cannot approve")))
    with pytest.raises(ForbiddenError):
        await decide_approval(uuid4(), ApprovalDecisionIn(status="APPROVED"), svc)


def test_decision_route_is_exposed_in_openapi():
    from src.main import app

    operation = app.openapi()["paths"]["/api/v1/finance/approvals/{approval_id}/decision"]["post"]
    assert "ApprovalDecisionIn" in str(operation["requestBody"])
    assert "ApprovalOut" in str(operation["responses"]["200"])
