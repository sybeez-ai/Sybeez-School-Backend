from datetime import date
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.fees.payment_service import PaymentService
from src.modules.fees.permissions import assert_not_teacher_finance
from src.modules.fees.schemas import (
    AdjustmentCreate,
    FeeCategoryCreate,
    FeeCategoryUpdate,
    FeeSettingsUpdate,
    FeeStructureCreate,
    FeeStructureUpdate,
    InstallmentIn,
    InvoiceGenerateIn,
    LateFeeRuleIn,
    ManualPaymentIn,
    PaymentOrderIn,
    PaymentVerifyIn,
    RefundIn,
    StructureItemIn,
)
from src.modules.fees.service import FeeService
from src.shared.enums import UserRole

router = APIRouter(tags=["fees"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/fees/health")
async def fees_health():
    return {"module": "fees", "status": "ok"}


# ── categories ────────────────────────────────────────────────


@router.get("/fees/categories")
async def list_categories(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).list_categories(_sid(user))


@router.post("/fees/categories", status_code=201)
async def create_category(
    body: FeeCategoryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).create_category(_sid(user), user.id, body)


@router.patch("/fees/categories/{category_id}")
async def update_category(
    category_id: UUID,
    body: FeeCategoryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).update_category(_sid(user), category_id, body)


# ── structures ────────────────────────────────────────────────


@router.get("/fees/structures")
async def list_structures(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    academic_year_id: UUID | None = None,
):
    return await FeeService(db).list_structures(_sid(user), academic_year_id)


@router.post("/fees/structures", status_code=201)
async def create_structure(
    body: FeeStructureCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).create_structure(_sid(user), user.id, body)


@router.get("/fees/structures/{structure_id}")
async def get_structure(
    structure_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).get_structure(_sid(user), structure_id)


@router.patch("/fees/structures/{structure_id}")
async def update_structure(
    structure_id: UUID,
    body: FeeStructureUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).update_structure(_sid(user), user.id, structure_id, body)


@router.post("/fees/structures/{structure_id}/publish")
async def publish_structure(
    structure_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).publish_structure(_sid(user), user.id, structure_id)


@router.post("/fees/structures/{structure_id}/items", status_code=201)
async def add_structure_item(
    structure_id: UUID,
    body: StructureItemIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).add_structure_item(_sid(user), structure_id, body)


@router.post("/fees/structures/{structure_id}/installments", status_code=201)
async def add_installment(
    structure_id: UUID,
    body: InstallmentIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).add_installment(_sid(user), structure_id, body)


# ── dashboard / students ──────────────────────────────────────


@router.get("/fees/dashboard")
async def fees_dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).dashboard(_sid(user))


@router.get("/fees/students")
async def list_fee_students(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    academic_year_id: UUID | None = None,
    grade_id: UUID | None = None,
    section_id: UUID | None = None,
):
    return await FeeService(db).list_student_summaries(
        _sid(user),
        academic_year_id=academic_year_id,
        grade_id=grade_id,
        section_id=section_id,
    )


@router.get("/fees/students/{student_id}")
async def student_fees(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT, UserRole.STUDENT)),
    ],
    academic_year_id: UUID | None = None,
):
    assert_not_teacher_finance(user.role.value)
    svc = FeeService(db)
    if user.role.value != UserRole.SCHOOL_ADMIN.value:
        allowed = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
        if student_id not in allowed:
            raise ForbiddenError("Not allowed")
    return await svc.student_summary(_sid(user), student_id, academic_year_id)


@router.get("/fees/students/{student_id}/ledger")
async def student_ledger(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT)),
    ],
):
    svc = FeeService(db)
    if user.role.value == UserRole.PARENT.value:
        allowed = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
        if student_id not in allowed:
            raise ForbiddenError("Not allowed")
    return await svc.ledger(_sid(user), student_id)


@router.post("/fees/accounts/{account_id}/adjustments", status_code=201)
async def add_adjustment(
    account_id: UUID,
    body: AdjustmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).add_adjustment(_sid(user), user.id, account_id, body)


# ── invoices ──────────────────────────────────────────────────


@router.get("/fees/invoices")
async def list_invoices(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT, UserRole.STUDENT)),
    ],
    status: str | None = None,
    student_id: UUID | None = None,
):
    assert_not_teacher_finance(user.role.value)
    svc = FeeService(db)
    if user.role.value != UserRole.SCHOOL_ADMIN.value:
        allowed = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
        if student_id and student_id not in allowed:
            raise ForbiddenError("Not allowed")
        if not student_id and len(allowed) == 1:
            student_id = allowed[0]
        elif not student_id:
            # aggregate for first child / all linked — return all for linked
            out = []
            for sid in allowed:
                out.extend(await svc.list_invoices(_sid(user), status=status, student_id=sid))
            return out
    return await svc.list_invoices(_sid(user), status=status, student_id=student_id)


@router.post("/fees/invoices/generate", status_code=201)
async def generate_invoices(
    body: InvoiceGenerateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).generate_invoices(_sid(user), user.id, body)


@router.get("/fees/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT, UserRole.STUDENT)),
    ],
):
    assert_not_teacher_finance(user.role.value)
    svc = FeeService(db)
    inv = await svc.get_invoice(_sid(user), invoice_id)
    if user.role.value != UserRole.SCHOOL_ADMIN.value:
        allowed = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
        if inv.student_id not in allowed:
            raise ForbiddenError("Not allowed")
    return inv


class CancelIn(BaseModel):
    reason: str = Field(min_length=1)


@router.post("/fees/invoices/{invoice_id}/cancel")
async def cancel_invoice(
    invoice_id: UUID,
    body: CancelIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).cancel_invoice(_sid(user), user.id, invoice_id, body.reason)


# ── payments ──────────────────────────────────────────────────


@router.get("/fees/payments")
async def list_payments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT)),
    ],
    student_id: UUID | None = None,
):
    svc = FeeService(db)
    ps = PaymentService(db)
    if user.role.value == UserRole.PARENT.value:
        allowed = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
        if student_id and student_id not in allowed:
            raise ForbiddenError("Not allowed")
        out = []
        for sid in ([student_id] if student_id else allowed):
            out.extend(await ps.list_payments(_sid(user), student_id=sid))
        return out
    return await ps.list_payments(_sid(user), student_id=student_id)


@router.post("/fees/payments/manual", status_code=201)
async def manual_payment(
    body: ManualPaymentIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await PaymentService(db).record_manual(_sid(user), user.id, body)


@router.post("/fees/payments/{payment_id}/refund")
async def refund_payment(
    payment_id: UUID,
    body: RefundIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await PaymentService(db).refund(_sid(user), user.id, payment_id, body)


@router.post("/payments/orders", status_code=201)
async def create_payment_order(
    body: PaymentOrderIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.PARENT, UserRole.SCHOOL_ADMIN)),
    ],
):
    return await PaymentService(db).create_order(
        _sid(user), user.id, user.role.value, body
    )


@router.post("/payments/verify")
async def verify_payment(
    body: PaymentVerifyIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.PARENT, UserRole.SCHOOL_ADMIN)),
    ],
):
    return await PaymentService(db).verify_payment(_sid(user), user.id, body)


@router.post("/payments/webhook/{provider}")
async def payment_webhook(
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    raw_body = await request.body()
    payload = json.loads(raw_body.decode("utf-8") or "{}")
    headers = {k.lower(): v for k, v in request.headers.items()}
    return await PaymentService(db).handle_webhook(provider, payload, headers, raw_body)


# ── receipts ──────────────────────────────────────────────────


@router.get("/fees/receipts/{receipt_id}")
async def get_receipt(
    receipt_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT, UserRole.STUDENT)),
    ],
):
    ps = PaymentService(db)
    await ps.assert_receipt_access(_sid(user), user.id, user.role.value, receipt_id)
    return await ps.get_receipt(_sid(user), receipt_id)


@router.get("/fees/receipts/{receipt_id}/pdf")
async def receipt_pdf(
    receipt_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PARENT, UserRole.STUDENT)),
    ],
):
    ps = PaymentService(db)
    await ps.assert_receipt_access(_sid(user), user.id, user.role.value, receipt_id)
    data = await ps.receipt_pdf_bytes(_sid(user), receipt_id)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="receipt-{receipt_id}.pdf"'},
    )


# ── parent me ─────────────────────────────────────────────────


@router.get("/fees/me/children")
async def my_children_fees(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    svc = FeeService(db)
    ids = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
    return [await svc.student_summary(_sid(user), sid) for sid in ids]


@router.get("/fees/me/children/{student_id}")
async def my_child_fees(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    svc = FeeService(db)
    ids = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
    if student_id not in ids:
        raise ForbiddenError("Not linked to this student")
    return await svc.student_summary(_sid(user), student_id)


@router.get("/fees/me/invoices")
async def my_invoices(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    svc = FeeService(db)
    ids = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
    out = []
    for sid in ids:
        out.extend(await svc.list_invoices(_sid(user), student_id=sid))
    return out


@router.get("/fees/me/payments")
async def my_payments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    svc = FeeService(db)
    ps = PaymentService(db)
    ids = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
    out = []
    for sid in ids:
        out.extend(await ps.list_payments(_sid(user), student_id=sid))
    return out


@router.get("/fees/me/receipts")
async def my_receipts(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    svc = FeeService(db)
    ps = PaymentService(db)
    ids = await svc._linked_student_ids(_sid(user), user.id, user.role.value)
    payments = []
    for sid in ids:
        payments.extend(await ps.list_payments(_sid(user), student_id=sid))
    out = []
    for p in payments:
        if p.receipt_id:
            out.append(await ps.get_receipt(_sid(user), p.receipt_id))
    return out


# ── settings / late fees / reports ────────────────────────────


@router.get("/fees/settings")
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).get_settings(_sid(user))


@router.patch("/fees/settings")
async def update_settings(
    body: FeeSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).update_settings(_sid(user), body)


@router.get("/fees/late-fee-rules")
async def list_late_rules(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).list_late_rules(_sid(user))


@router.post("/fees/late-fee-rules", status_code=201)
async def create_late_rule(
    body: LateFeeRuleIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).upsert_late_rule(_sid(user), body)


@router.post("/fees/late-fees/apply")
async def apply_late_fees(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    n = await FeeService(db).apply_late_fees(_sid(user), user.id)
    return {"applied": n}


@router.post("/fees/overdue/refresh")
async def refresh_overdue(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    n = await FeeService(db).refresh_overdue(_sid(user))
    return {"updated": n}


@router.post("/fees/reminders/run")
async def run_fee_reminders(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await FeeService(db).send_invoice_reminders(_sid(user))


@router.get("/fees/reports/collection")
async def report_collection(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = Query("json"),
):
    rows = await FeeService(db).report_collection(
        _sid(user), from_date=from_date, to_date=to_date
    )
    if format == "csv":
        csv_data = await FeeService(db).report_csv(
            [{"date": str(r.date), "method": r.method, "total": str(r.total)} for r in rows],
            ["date", "method", "total"],
        )
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=collection.csv"},
        )
    return rows


@router.get("/fees/reports/outstanding")
async def report_outstanding(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    format: str = Query("json"),
):
    rows = await FeeService(db).report_outstanding(_sid(user))
    if format == "csv":
        csv_data = await FeeService(db).report_csv(
            [
                {
                    "student": r.student_name,
                    "class": r.class_name or "",
                    "invoice": r.invoice_number,
                    "due": str(r.due_date),
                    "balance": str(r.balance_amount),
                    "status": r.status,
                    "days_overdue": r.days_overdue,
                }
                for r in rows
            ],
            ["student", "class", "invoice", "due", "balance", "status", "days_overdue"],
        )
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=outstanding.csv"},
        )
    return rows


@router.get("/fees/reports/overdue")
async def report_overdue(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    rows = await FeeService(db).report_outstanding(_sid(user))
    return [r for r in rows if r.days_overdue > 0]
