"""Fee-desk routes the web portal calls under /api/v1/fees.

These delegate to the same school-scoped finance service as /api/v1/finance.
X-Branch-Id, when sent, limits the call to one branch of the token's school.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.fees.payment_service import PaymentService
from src.modules.fees.schemas import ReceiptOut
from src.modules.finance.permissions import assert_finance
from src.modules.finance.router import _export, _svc, finance_db
from src.modules.finance.schemas import (
    ApprovalOut,
    AuditRow,
    BounceIn,
    CancelReceiptIn,
    ChequeOut,
    CollectIn,
    CollectionOut,
    ConcessionOut,
    ConcessionRequestIn,
    DayCloseIn,
    DayCloseOut,
    DecisionIn,
    FineWaiverIn,
    ReminderLogOut,
)
from src.modules.finance.service import FinanceService

desk = APIRouter(tags=["fees-desk"])


@desk.post("/fees/collections", status_code=201, response_model=CollectionOut)
async def desk_collect(body: CollectIn, svc: Annotated[FinanceService, Depends(_svc)]):
    """Counter collection: several fee heads and split tenders on one receipt."""
    assert_finance(svc.role, "collect")
    return await svc.collect(body)


@desk.get("/fees/concessions", response_model=list[ConcessionOut])
async def desk_list_concessions(
    svc: Annotated[FinanceService, Depends(_svc)], status: str | None = None
):
    assert_finance(svc.role, "reports.read")
    return await svc.list_concessions(status)


@desk.post("/fees/concessions", status_code=201, response_model=ConcessionOut)
async def desk_request_concession(
    body: ConcessionRequestIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "concession.request")
    return await svc.request_concession(body)


@desk.post("/fees/concessions/{concession_id}/approve", response_model=ConcessionOut)
async def desk_approve_concession(
    concession_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "approve")
    return await svc.decide_concession(concession_id, True, body.note)


@desk.post("/fees/concessions/{concession_id}/reject", response_model=ConcessionOut)
async def desk_reject_concession(
    concession_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "approve")
    return await svc.decide_concession(concession_id, False, body.note)


@desk.post("/fees/fine-waivers", status_code=201, response_model=ApprovalOut)
async def desk_request_waiver(body: FineWaiverIn, svc: Annotated[FinanceService, Depends(_svc)]):
    """Ask a different user to waive a fine. Does not change the invoice until approved."""
    assert_finance(svc.role, "concession.request")
    return await svc.request_waiver(body)


@desk.get("/fees/cheques", response_model=list[ChequeOut])
async def desk_list_cheques(svc: Annotated[FinanceService, Depends(_svc)], status: str | None = None):
    assert_finance(svc.role, "reports.read")
    return await svc.list_cheques(status)


@desk.post("/fees/cheques/{tender_id}/deposit", response_model=ChequeOut)
async def desk_deposit_cheque(tender_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "cheque.deposit")
    return await svc.deposit_cheque(tender_id)


@desk.post("/fees/cheques/{tender_id}/clear", response_model=ChequeOut)
async def desk_clear_cheque(tender_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "cheque.clear")
    return await svc.clear_cheque(tender_id)


@desk.post("/fees/cheques/{tender_id}/bounce", response_model=ChequeOut)
async def desk_bounce_cheque(
    tender_id: UUID, body: BounceIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "cheque.bounce")
    return await svc.bounce_cheque(tender_id, body)


@desk.get("/fees/receipts", response_model=list[ReceiptOut])
async def desk_list_receipts(
    svc: Annotated[FinanceService, Depends(_svc)],
    status: str | None = None,
    limit: int = Query(100, ge=1, le=200),
):
    assert_finance(svc.role, "ledger.read")
    return await svc.list_receipts(status, limit)


@desk.post("/fees/receipts/{receipt_id}/cancel", status_code=201, response_model=ApprovalOut)
async def desk_cancel_receipt(
    receipt_id: UUID, body: CancelReceiptIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    """Queue a receipt cancellation. A different user approves it."""
    assert_finance(svc.role, "receipt.cancel.request")
    return await svc.request_receipt_cancel(receipt_id, body)


@desk.post("/fees/receipts/{receipt_id}/duplicate", response_model=ReceiptOut)
async def desk_duplicate_receipt(
    receipt_id: UUID,
    svc: Annotated[FinanceService, Depends(_svc)],
    db: Annotated[AsyncSession, Depends(finance_db)],
):
    """Mark the next copy as DUPLICATE and return the receipt. PDF: GET .../pdf?duplicate=true."""
    assert_finance(svc.role, "collect")
    await svc.mark_reprint(receipt_id)
    return await PaymentService(db).get_receipt(svc.school_id, receipt_id)


@desk.post("/fees/day-close", status_code=201, response_model=DayCloseOut)
async def desk_submit_day_close(body: DayCloseIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "day_close.submit")
    return await svc.submit_day_close(body)


@desk.get("/fees/day-close", response_model=list[DayCloseOut])
async def desk_list_day_close(
    svc: Annotated[FinanceService, Depends(_svc)], business_date: date | None = None
):
    assert_finance(svc.role, "reports.read")
    return await svc.list_day_close(business_date)


@desk.post("/fees/day-close/{day_close_id}/verify", response_model=DayCloseOut)
async def desk_verify_day_close(
    day_close_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "day_close.verify")
    return await svc.verify_day_close(day_close_id, True, body.note)


@desk.post("/fees/day-close/{day_close_id}/reject", response_model=DayCloseOut)
async def desk_reject_day_close(
    day_close_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "day_close.verify")
    return await svc.verify_day_close(day_close_id, False, body.note)


@desk.get("/fees/reports/head-wise")
async def desk_report_head(
    svc: Annotated[FinanceService, Depends(_svc)],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_head_wise(from_date, to_date)
    return _export(rows, ["head", "amount"], format, "head-wise", "Head-wise collection")


@desk.get("/fees/reports/collector-wise")
@desk.get("/fees/reports/collector")
async def desk_report_collector(
    svc: Annotated[FinanceService, Depends(_svc)],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_collector_wise(from_date, to_date)
    return _export(rows, ["collector", "amount"], format, "collector-wise", "Collector-wise collection")


@desk.get("/fees/reports/concessions")
async def desk_report_concessions(
    svc: Annotated[FinanceService, Depends(_svc)],
    status: str | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_concessions(status)
    cols = ["student", "student_id", "status", "value_type", "value", "amount", "reason"]
    return _export(rows, cols, format, "concessions", "Concessions")


@desk.get("/fees/reports/cancelled-receipts")
async def desk_report_cancelled(
    svc: Annotated[FinanceService, Depends(_svc)], format: str = Query("json")
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_cancelled_receipts()
    cols = ["receipt", "student", "amount", "reason", "cancelled_at"]
    return _export(rows, cols, format, "cancelled-receipts", "Cancelled receipts")


@desk.get("/fees/reminders", response_model=list[ReminderLogOut])
@desk.get("/fees/reminder-log", response_model=list[ReminderLogOut])
async def desk_reminder_log(
    svc: Annotated[FinanceService, Depends(_svc)],
    limit: int = Query(200, ge=1, le=500),
):
    assert_finance(svc.role, "reports.read")
    return await svc.list_reminder_log(limit)


@desk.get("/fees/audit", response_model=list[AuditRow])
async def desk_audit_log(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "audit.read")
    return await svc.list_audit()


@desk.get("/fees/approvals", response_model=list[ApprovalOut])
async def desk_list_approvals(
    svc: Annotated[FinanceService, Depends(_svc)], status: str | None = None
):
    assert_finance(svc.role, "reports.read")
    return await svc.list_approvals(status)


@desk.post("/fees/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def desk_approve(approval_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]):
    """Approve a queued receipt cancellation, fine waiver, concession, or refund."""
    assert_finance(svc.role, "approve")
    return await svc.decide_approval(approval_id, True, body.note)


@desk.post("/fees/approvals/{approval_id}/reject", response_model=ApprovalOut)
async def desk_reject(approval_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "approve")
    return await svc.decide_approval(approval_id, False, body.note)
