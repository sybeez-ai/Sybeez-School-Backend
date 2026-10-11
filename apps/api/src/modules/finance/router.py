"""Finance portal HTTP API. Accounts staff only; every handler is school-scoped."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.fees.payment_service import PaymentService
from src.modules.fees.route_reports import RouteReportService
from src.modules.fees.route_schemas import (
    PlatformFeeReportOut,
    ReconciliationRowOut,
    RouteAccountIn,
    RouteSettlementIn,
    RouteStakeholderIn,
    RouteStatusOut,
    TransferRowOut,
)
from src.modules.fees.route_service import RouteService
from src.modules.fees.service import FeeService
from src.modules.fees.permissions import FINANCE_VIEW
from src.modules.fees.schemas import ReceiptOut, RefundIn, RefundOut
from src.modules.fees.tenant import bind_tenant
from src.modules.finance.exports import rows_to_csv, rows_to_pdf, rows_to_xlsx
from src.modules.access.deps import effective_permissions
from src.modules.finance.permissions import assert_finance, bind_finance_permissions, permissions_for
from src.modules.finance.schemas import (
    AccessOut,
    BranchIn,
    BranchPatch,
    BounceIn,
    CancelReceiptIn,
    CollectIn,
    ConcessionRequestIn,
    ConcessionRuleIn,
    ConcessionRulePatch,
    ApprovalOut,
    AuditRow,
    BranchOut,
    ChequeOut,
    CollectionOut,
    ConcessionOut,
    ConcessionRuleOut,
    DashboardOut,
    DayCloseIn,
    DayCloseOut,
    DecisionIn,
    DueLine,
    FeeProfileIn,
    FeeProfileOut,
    FinanceNotificationOut,
    FinancialYearIn,
    FinancialYearOut,
    FineWaiverIn,
    LedgerRow,
    ReminderQueuedOut,
    SiblingGroupIn,
    SiblingGroupOut,
    StaffScopeIn,
    StaffScopeOut,
    StudentSearchHit,
)
from src.modules.finance.service import FinanceService
from src.shared.enums import UserRole
from src.modules.access.deps import public

router = APIRouter(prefix="/finance", tags=["finance"])


async def finance_db(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> AsyncSession:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id)
    return db


def _user_staff(
    user: Annotated[CurrentUser, Depends(require_roles(*FINANCE_VIEW))],
) -> CurrentUser:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user


async def _svc(
    db: Annotated[AsyncSession, Depends(finance_db)],
    user: Annotated[CurrentUser, Depends(_user_staff)],
    x_branch_id: Annotated[UUID | None, Header(alias="X-Branch-Id")] = None,
) -> FinanceService:
    """Staff session. X-Branch-Id narrows every query to one branch of this school."""
    bind_finance_permissions(user.role.value, await effective_permissions(db, user))
    return await FinanceService(db, user.school_id, user.id, user.role.value).prepare(x_branch_id)


def _export(rows: list[dict], columns: list[str], fmt: str, filename: str, title: str):
    if fmt in {"csv"}:
        return Response(
            content=rows_to_csv(rows, columns),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )
    if fmt in {"xlsx", "excel"}:
        return Response(
            content=rows_to_xlsx(title, rows, columns),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )
    if fmt == "pdf":
        return Response(
            content=rows_to_pdf(title, rows, columns),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
        )
    return rows


@router.get("/health")
@public
async def finance_health():
    return {"module": "finance", "status": "ok"}


@router.get("/access", response_model=AccessOut)
async def finance_access(svc: Annotated[FinanceService, Depends(_svc)]):
    return AccessOut(
        role=svc.role,
        school_id=svc.school_id,
        permissions=permissions_for(svc.role),
        branch_ids=sorted(svc.branch_ids) if svc.branch_ids is not None else None,
    )


@router.get("/branches", response_model=list[BranchOut])
async def list_branches(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await svc.list_branches()


@router.post("/branches", status_code=201, response_model=BranchOut)
async def create_branch(body: BranchIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "branches.write")
    return await svc.create_branch(body)


@router.patch("/branches/{branch_id}", response_model=BranchOut)
async def update_branch(branch_id: UUID, body: BranchPatch, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "branches.write")
    return await svc.update_branch(branch_id, body)


@router.get("/financial-years", response_model=list[FinancialYearOut])
async def list_years(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await svc.list_years()


@router.post("/financial-years", status_code=201, response_model=FinancialYearOut)
async def create_year(body: FinancialYearIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "fy.write")
    return await svc.create_year(body)


@router.post("/financial-years/{year_id}/close", response_model=FinancialYearOut)
async def close_year(year_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "fy.write")
    return await svc.close_year(year_id)


@router.get("/staff-scopes", response_model=list[StaffScopeOut])
async def list_scopes(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await svc.list_scopes()


@router.post("/staff-scopes", status_code=201, response_model=StaffScopeOut)
async def add_scope(body: StaffScopeIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "staff_scope.write")
    return await svc.add_scope(body)


@router.get("/students/search", response_model=list[StudentSearchHit])
async def search_students(svc: Annotated[FinanceService, Depends(_svc)], q: str = Query(min_length=1)):
    assert_finance(svc.role, "ledger.read")
    return await svc.search_students(q)


@router.get("/students/{student_id}/profile", response_model=FeeProfileOut)
async def get_profile(student_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await svc.get_profile(student_id)


@router.put("/students/{student_id}/profile", response_model=FeeProfileOut)
async def put_profile(student_id: UUID, body: FeeProfileIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "profile.write")
    return await svc.upsert_profile(student_id, body)


@router.get("/sibling-groups", response_model=list[SiblingGroupOut])
async def list_sibling_groups(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await svc.list_sibling_groups()


@router.post("/sibling-groups", status_code=201, response_model=SiblingGroupOut)
async def create_sibling_group(body: SiblingGroupIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "profile.write")
    return await svc.create_sibling_group(body)


@router.get("/concession-rules", response_model=list[ConcessionRuleOut])
async def list_rules(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await svc.list_rules()


@router.post("/concession-rules", status_code=201, response_model=ConcessionRuleOut)
async def create_rule(body: ConcessionRuleIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "concession_rule.write")
    return await svc.create_rule(body)


@router.patch("/concession-rules/{rule_id}", response_model=ConcessionRuleOut)
async def patch_rule(rule_id: UUID, body: ConcessionRulePatch, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "concession_rule.write")
    return await svc.patch_rule(rule_id, body)


@router.get("/concessions", response_model=list[ConcessionOut])
async def list_concessions(
    svc: Annotated[FinanceService, Depends(_svc)], status: str | None = None
):
    assert_finance(svc.role, "reports.read")
    return await svc.list_concessions(status)


@router.post("/concessions", status_code=201, response_model=ConcessionOut)
async def request_concession(body: ConcessionRequestIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "concession.request")
    return await svc.request_concession(body)


@router.post("/concessions/{concession_id}/approve", response_model=ConcessionOut)
async def approve_concession(
    concession_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "approve")
    return await svc.decide_concession(concession_id, True, body.note)


@router.post("/concessions/{concession_id}/reject", response_model=ConcessionOut)
async def reject_concession(
    concession_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "approve")
    return await svc.decide_concession(concession_id, False, body.note)


@router.get("/students/{student_id}/dues", response_model=list[DueLine])
async def student_dues(student_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await svc.dues(student_id)


@router.post("/collections", status_code=201, response_model=CollectionOut)
async def collect(body: CollectIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "collect")
    return await svc.collect(body)


@router.get("/receipts/{receipt_id}", response_model=ReceiptOut)
async def finance_receipt(
    receipt_id: UUID,
    svc: Annotated[FinanceService, Depends(_svc)],
    db: Annotated[AsyncSession, Depends(finance_db)],
):
    assert_finance(svc.role, "ledger.read")
    ps = PaymentService(db)
    return await ps.get_receipt(svc.school_id, receipt_id)


@router.get("/receipts/{receipt_id}/pdf")
async def finance_receipt_pdf(
    receipt_id: UUID,
    svc: Annotated[FinanceService, Depends(_svc)],
    db: Annotated[AsyncSession, Depends(finance_db)],
    duplicate: bool = False,
):
    assert_finance(svc.role, "ledger.read")
    if duplicate:
        await svc.mark_reprint(receipt_id)
    ps = PaymentService(db)
    data = await ps.receipt_pdf_bytes(svc.school_id, receipt_id)
    return Response(content=data, media_type="application/pdf")


@router.post("/receipts/{receipt_id}/reprint")
async def reprint_receipt(receipt_id: UUID, svc: Annotated[FinanceService, Depends(_svc)], db: Annotated[AsyncSession, Depends(finance_db)]):
    assert_finance(svc.role, "collect")
    await svc.mark_reprint(receipt_id)
    data = await PaymentService(db).receipt_pdf_bytes(svc.school_id, receipt_id)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="receipt-duplicate.pdf"'},
    )


@router.post("/receipts/{receipt_id}/cancellations", status_code=201, response_model=ApprovalOut)
async def cancel_receipt(receipt_id: UUID, body: CancelReceiptIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "receipt.cancel.request")
    return await svc.request_receipt_cancel(receipt_id, body)


@router.get("/cheques", response_model=list[ChequeOut])
async def list_cheques(svc: Annotated[FinanceService, Depends(_svc)], status: str | None = None):
    assert_finance(svc.role, "reports.read")
    return await svc.list_cheques(status)


@router.post("/cheques/{tender_id}/deposit", response_model=ChequeOut)
async def deposit_cheque(tender_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "cheque.deposit")
    return await svc.deposit_cheque(tender_id)


@router.post("/cheques/{tender_id}/clear", response_model=ChequeOut)
async def clear_cheque(tender_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "cheque.clear")
    return await svc.clear_cheque(tender_id)


@router.post("/cheques/{tender_id}/bounce", response_model=ChequeOut)
async def bounce_cheque(tender_id: UUID, body: BounceIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "cheque.bounce")
    return await svc.bounce_cheque(tender_id, body)


@router.get("/students/{student_id}/ledger", response_model=list[LedgerRow])
async def student_ledger(student_id: UUID, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await svc.ledger(student_id)


@router.post("/day-close", status_code=201, response_model=DayCloseOut)
async def submit_day_close(body: DayCloseIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "day_close.submit")
    return await svc.submit_day_close(body)


@router.get("/day-close", response_model=list[DayCloseOut])
async def list_day_close(
    svc: Annotated[FinanceService, Depends(_svc)], business_date: date | None = None
):
    assert_finance(svc.role, "reports.read")
    return await svc.list_day_close(business_date)


@router.post("/day-close/{day_close_id}/verify", response_model=DayCloseOut)
async def verify_day_close(
    day_close_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "day_close.verify")
    return await svc.verify_day_close(day_close_id, True, body.note)


@router.post("/day-close/{day_close_id}/reject", response_model=DayCloseOut)
async def reject_day_close(
    day_close_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "day_close.verify")
    return await svc.verify_day_close(day_close_id, False, body.note)


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "dashboard.read")
    return await svc.dashboard()


@router.get("/reports/defaulters")
async def report_defaulters(svc: Annotated[FinanceService, Depends(_svc)], format: str = Query("json")):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_defaulters()
    cols = ["student", "student_id", "invoice", "due_date", "balance", "days_overdue", "status"]
    return _export(rows, cols, format, "defaulters", "Defaulters")


@router.get("/reports/day-book")
async def report_day_book(
    svc: Annotated[FinanceService, Depends(_svc)],
    on: date = Query(default_factory=date.today),
    branch_id: UUID | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_day_book(on, branch_id)
    cols = ["receipt", "status", "student", "amount", "method", "collector", "time"]
    return _export(rows, cols, format, "day-book", "Day book")


@router.get("/reports/head-wise")
async def report_head(
    svc: Annotated[FinanceService, Depends(_svc)],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_head_wise(from_date, to_date)
    return _export(rows, ["head", "amount"], format, "head-wise", "Head-wise collection")


@router.get("/reports/mode-wise")
async def report_mode(
    svc: Annotated[FinanceService, Depends(_svc)],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_mode_wise(from_date, to_date)
    return _export(rows, ["method", "amount"], format, "mode-wise", "Mode-wise collection")


@router.get("/reports/collector-wise")
async def report_collector(
    svc: Annotated[FinanceService, Depends(_svc)],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_collector_wise(from_date, to_date)
    return _export(rows, ["collector", "amount"], format, "collector-wise", "Collector-wise collection")


@router.get("/reports/branch-wise")
async def report_branch(
    svc: Annotated[FinanceService, Depends(_svc)],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_branch_wise(from_date, to_date)
    return _export(rows, ["branch", "branch_id", "amount"], format, "branch-wise", "Branch-wise collection")


@router.get("/reports/category-wise")
async def report_category(
    svc: Annotated[FinanceService, Depends(_svc)],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_category_wise(from_date, to_date)
    return _export(rows, ["category", "amount"], format, "category-wise", "Category-wise collection")


@router.get("/notifications", response_model=list[FinanceNotificationOut])
async def finance_notifications(
    svc: Annotated[FinanceService, Depends(_svc)],
    limit: int = Query(50, ge=1, le=200),
):
    """Fee notifications for this user in this school. Other schools stay hidden."""
    assert_finance(svc.role, "dashboard.read")
    return await svc.list_notifications(limit)


@router.get("/reports/concessions")
async def report_concessions(
    svc: Annotated[FinanceService, Depends(_svc)],
    status: str | None = None,
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_concessions(status)
    cols = ["student", "student_id", "status", "value_type", "value", "amount", "reason"]
    return _export(rows, cols, format, "concessions", "Concessions")


@router.get("/reports/cancelled-receipts")
async def report_cancelled_receipts(
    svc: Annotated[FinanceService, Depends(_svc)],
    format: str = Query("json"),
):
    assert_finance(svc.role, "reports.read")
    rows = await svc.report_cancelled_receipts()
    cols = ["receipt", "student", "amount", "reason", "cancelled_at"]
    return _export(rows, cols, format, "cancelled-receipts", "Cancelled receipts")


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(svc: Annotated[FinanceService, Depends(_svc)], status: str | None = None):
    assert_finance(svc.role, "reports.read")
    return await svc.list_approvals(status)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve(approval_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "approve")
    return await svc.decide_approval(approval_id, True, body.note)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
async def reject(approval_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "approve")
    return await svc.decide_approval(approval_id, False, body.note)


@router.post("/fine-waivers", status_code=201, response_model=ApprovalOut)
async def request_waiver(body: FineWaiverIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "concession.request")
    return await svc.request_waiver(body)


@router.post("/payments/{payment_id}/refunds", status_code=201, response_model=RefundOut)
async def request_refund(payment_id: UUID, body: RefundIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "refund.request")
    return await svc.request_refund(payment_id, body)


@router.post("/refunds/{refund_id}/approve", response_model=RefundOut)
async def approve_refund(refund_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "approve")
    return await svc.payments.approve_refund(svc.school_id, svc.user_id, refund_id, note=body.note)


@router.post("/refunds/{refund_id}/reject", response_model=RefundOut)
async def reject_refund(refund_id: UUID, body: DecisionIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "approve")
    return await svc.payments.reject_refund(
        svc.school_id, svc.user_id, refund_id, note=body.note or "Rejected"
    )


@router.get("/audit", response_model=list[AuditRow])
async def audit_log(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "audit.read")
    return await svc.list_audit()


@router.post("/reminders/schedule", status_code=202, response_model=ReminderQueuedOut)
async def schedule_reminders(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reminders.run")
    return await svc.schedule_reminders()


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/route", response_model=RouteStatusOut)
async def finance_route_status(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await RouteService(svc.db).status(svc.school_id)


@router.post("/route/account", response_model=RouteStatusOut)
async def finance_route_account(
    body: RouteAccountIn, request: Request, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "route.manage")
    return await RouteService(svc.db).create_account(svc.school_id, svc.user_id, body, ip=_ip(request))


@router.post("/route/stakeholder", response_model=RouteStatusOut)
async def finance_route_stakeholder(
    body: RouteStakeholderIn, request: Request, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "route.manage")
    return await RouteService(svc.db).add_stakeholder(svc.school_id, svc.user_id, body, ip=_ip(request))


@router.post("/route/settlement", response_model=RouteStatusOut)
async def finance_route_settlement(
    body: RouteSettlementIn, request: Request, svc: Annotated[FinanceService, Depends(_svc)]
):
    assert_finance(svc.role, "route.manage")
    return await RouteService(svc.db).configure_settlement(svc.school_id, svc.user_id, body, ip=_ip(request))


@router.post("/route/refresh", response_model=RouteStatusOut)
async def finance_route_refresh(request: Request, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "route.manage")
    return await RouteService(svc.db).refresh(svc.school_id, svc.user_id, ip=_ip(request))


@router.get("/route/reports/transfers", response_model=list[TransferRowOut])
async def finance_route_transfers(
    svc: Annotated[FinanceService, Depends(_svc)], status: str | None = None
):
    assert_finance(svc.role, "reports.read")
    return await RouteReportService(svc.db).transfers(svc.school_id, status)


@router.get("/route/reports/failed-transfers", response_model=list[TransferRowOut])
async def finance_route_failed(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await RouteReportService(svc.db).failed_transfers(svc.school_id)


@router.get("/route/reports/platform-fees", response_model=PlatformFeeReportOut)
async def finance_route_platform_fees(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    return await RouteReportService(svc.db).platform_fees(svc.school_id)


@router.get("/route/reports/reconciliation", response_model=list[ReconciliationRowOut])
async def finance_route_reconciliation(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "reports.read")
    settings = await FeeService(svc.db).get_or_create_settings(svc.school_id)
    return await RouteReportService(svc.db).reconciliation(
        svc.school_id, allow_platform_settlement=bool(settings.allow_platform_settlement)
    )
