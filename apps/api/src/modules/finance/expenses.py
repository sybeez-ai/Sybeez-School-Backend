"""Vendor expenses with GST, approval, and an append-only ledger."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.db.models import ExpenseHead, ExpenseLedgerEntry, SchoolExpense
from src.modules.access.audit import write_audit
from src.modules.access.deps import effective_permissions
from src.modules.finance.permissions import assert_finance, bind_finance_permissions
from src.modules.finance.router import _svc
from src.modules.finance.service import FinanceService
from src.modules.payroll.ledger import is_salary_voucher
from src.shared.utils import utcnow

_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_MODES = {"CASH", "BANK", "UPI", "CHEQUE"}

router = APIRouter(prefix="/finance", tags=["finance-expenses"])


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _gst(taxable: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    gst_amount = _money(taxable * rate / Decimal("100"))
    return gst_amount, _money(taxable + gst_amount)


class HeadIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=120)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned.replace("_", "").isalnum():
            raise ValueError("code must be letters, digits, or underscore")
        return cleaned


class ExpenseIn(BaseModel):
    head_id: UUID
    vendor_name: str = Field(min_length=2, max_length=200)
    vendor_gstin: str | None = Field(default=None, max_length=15)
    invoice_number: str | None = Field(default=None, max_length=64)
    invoice_date: date
    taxable_amount: Decimal = Field(gt=0)
    gst_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    payment_mode: str = "BANK"
    narration: str | None = Field(default=None, max_length=2000)
    branch_id: UUID | None = None

    @field_validator("vendor_gstin")
    @classmethod
    def _gstin(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip().upper()
        if not _GSTIN.match(cleaned):
            raise ValueError("vendor_gstin is not a valid GSTIN")
        return cleaned

    @field_validator("payment_mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in _MODES:
            raise ValueError("payment_mode must be CASH, BANK, UPI, or CHEQUE")
        return cleaned


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class PayIn(BaseModel):
    paid_on: date
    note: str | None = Field(default=None, max_length=2000)


def _head_out(row: ExpenseHead) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "is_active": row.is_active,
    }


def _expense_out(row: SchoolExpense) -> dict:
    return {
        "id": row.id,
        "head_id": row.head_id,
        "voucher_no": row.voucher_no,
        "vendor_name": row.vendor_name,
        "vendor_gstin": row.vendor_gstin,
        "invoice_number": row.invoice_number,
        "invoice_date": row.invoice_date,
        "taxable_amount": str(row.taxable_amount),
        "gst_rate": str(row.gst_rate),
        "gst_amount": str(row.gst_amount),
        "total_amount": str(row.total_amount),
        "payment_mode": row.payment_mode,
        "status": row.status,
        "narration": row.narration,
        "branch_id": row.branch_id,
        "paid_on": row.paid_on,
        "decision_note": row.decision_note,
    }


class ExpenseService:
    def __init__(self, finance: FinanceService):
        self.db = finance.db
        self.school_id = finance.school_id
        self.user_id = finance.user_id
        self.branch_ids = finance.branch_ids

    def _limit_branch(self, branch_id: UUID | None) -> UUID | None:
        if self.branch_ids is not None:
            if branch_id and branch_id not in self.branch_ids:
                raise ForbiddenError("Branch is outside your assignment")
            if branch_id:
                return branch_id
        return branch_id

    async def list_heads(self) -> list[dict]:
        rows = (
            await self.db.execute(
                select(ExpenseHead)
                .where(ExpenseHead.school_id == self.school_id)
                .order_by(ExpenseHead.code)
            )
        ).scalars()
        return [_head_out(row) for row in rows]

    async def create_head(self, data: HeadIn) -> dict:
        exists = (
            await self.db.execute(
                select(ExpenseHead).where(
                    ExpenseHead.school_id == self.school_id,
                    func.lower(ExpenseHead.code) == data.code.lower(),
                )
            )
        ).scalar_one_or_none()
        if exists:
            raise AppError(f"Expense head {data.code} already exists", status_code=422)
        row = ExpenseHead(school_id=self.school_id, code=data.code, name=data.name.strip(), is_active=True)
        self.db.add(row)
        await self.db.flush()
        await write_audit(
            self.db,
            school_id=self.school_id,
            actor_id=self.user_id,
            action="finance.expense_head.create",
            resource_type="expense_head",
            resource_id=str(row.id),
            message=f"Expense head {row.code} created",
            details={"code": row.code},
        )
        return _head_out(row)

    async def _next_voucher(self) -> str:
        count = await self.db.scalar(
            select(func.count()).select_from(SchoolExpense).where(SchoolExpense.school_id == self.school_id)
        )
        return f"EXP-{int(count or 0) + 1:05d}"

    async def _head(self, head_id: UUID) -> ExpenseHead:
        row = await self.db.get(ExpenseHead, head_id)
        if row is None or row.school_id != self.school_id or not row.is_active:
            raise NotFoundError("Expense head not found")
        return row

    async def create_expense(self, data: ExpenseIn) -> dict:
        await self._head(data.head_id)
        branch_id = self._limit_branch(data.branch_id)
        taxable = _money(data.taxable_amount)
        gst_amount, total = _gst(taxable, _money(data.gst_rate))
        row = SchoolExpense(
            school_id=self.school_id,
            head_id=data.head_id,
            voucher_no=await self._next_voucher(),
            vendor_name=data.vendor_name.strip(),
            vendor_gstin=data.vendor_gstin,
            invoice_number=(data.invoice_number or "").strip() or None,
            invoice_date=data.invoice_date,
            taxable_amount=taxable,
            gst_rate=_money(data.gst_rate),
            gst_amount=gst_amount,
            total_amount=total,
            payment_mode=data.payment_mode,
            status="DRAFT",
            narration=(data.narration or "").strip() or None,
            branch_id=branch_id,
            requested_by=self.user_id,
        )
        self.db.add(row)
        await self.db.flush()
        await write_audit(
            self.db,
            school_id=self.school_id,
            actor_id=self.user_id,
            action="finance.expense.create",
            resource_type="school_expense",
            resource_id=str(row.id),
            message=f"Expense {row.voucher_no} drafted",
            details={"total": str(total), "gst": str(gst_amount)},
        )
        return _expense_out(row)

    async def list_expenses(self, status: str | None) -> list[dict]:
        stmt = select(SchoolExpense).where(SchoolExpense.school_id == self.school_id)
        if status:
            stmt = stmt.where(SchoolExpense.status == status.upper())
        if self.branch_ids is not None:
            stmt = stmt.where(SchoolExpense.branch_id.in_(self.branch_ids))
        rows = (await self.db.execute(stmt.order_by(SchoolExpense.created_at.desc()))).scalars()
        return [_expense_out(row) for row in rows]

    async def _expense(self, expense_id: UUID) -> SchoolExpense:
        row = await self.db.get(SchoolExpense, expense_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Expense not found")
        if self.branch_ids is not None and row.branch_id not in self.branch_ids:
            raise ForbiddenError("Branch is outside your assignment")
        return row

    async def submit(self, expense_id: UUID) -> dict:
        row = await self._expense(expense_id)
        if row.status != "DRAFT":
            raise AppError("Only a draft expense can be submitted", status_code=422)
        row.status = "SUBMITTED"
        await self.db.flush()
        await write_audit(
            self.db,
            school_id=self.school_id,
            actor_id=self.user_id,
            action="finance.expense.submit",
            resource_type="school_expense",
            resource_id=str(row.id),
            message=f"Expense {row.voucher_no} submitted",
        )
        return _expense_out(row)

    async def _post(self, row: SchoolExpense, when: date, debit_account: str, credit_account: str, narration: str) -> None:
        now = datetime.now(timezone.utc)
        self.db.add(
            ExpenseLedgerEntry(
                school_id=self.school_id,
                expense_id=row.id,
                entry_date=when,
                account=debit_account,
                debit=row.total_amount,
                credit=Decimal("0.00"),
                narration=narration,
                actor_user_id=self.user_id,
                created_at=now,
            )
        )
        self.db.add(
            ExpenseLedgerEntry(
                school_id=self.school_id,
                expense_id=row.id,
                entry_date=when,
                account=credit_account,
                debit=Decimal("0.00"),
                credit=row.total_amount,
                narration=narration,
                actor_user_id=self.user_id,
                created_at=now,
            )
        )

    async def decide(self, expense_id: UUID, approve: bool, note: str | None) -> dict:
        row = await self._expense(expense_id)
        if row.status != "SUBMITTED":
            raise AppError("Only a submitted expense can be decided", status_code=422)
        if row.requested_by == self.user_id:
            raise ForbiddenError("Another person must approve this expense")
        row.approved_by = self.user_id
        row.decided_at = utcnow()
        row.decision_note = (note or "").strip() or None
        if approve:
            row.status = "APPROVED"
            await self._post(
                row,
                row.invoice_date,
                "EXPENSE",
                "PAYABLE",
                f"Approve {row.voucher_no}",
            )
        else:
            if not row.decision_note:
                raise AppError("A note is required to reject an expense", status_code=422)
            row.status = "REJECTED"
        await self.db.flush()
        await write_audit(
            self.db,
            school_id=self.school_id,
            actor_id=self.user_id,
            action="finance.expense.approve" if approve else "finance.expense.reject",
            resource_type="school_expense",
            resource_id=str(row.id),
            message=f"Expense {row.voucher_no} {row.status.lower()}",
            details={"total": str(row.total_amount)},
        )
        return _expense_out(row)

    async def pay(self, expense_id: UUID, data: PayIn) -> dict:
        row = await self._expense(expense_id)
        if row.status != "APPROVED":
            raise AppError("Only an approved expense can be paid", status_code=422)
        if is_salary_voucher(row):
            # Salary vouchers are split across net pay and statutory payables; payroll bank pay settles net pay.
            raise AppError(
                "Salary vouchers are paid from Payroll > Bank pay, not from expenses",
                code="salary_voucher_paid_in_payroll",
                status_code=422,
            )
        row.status = "PAID"
        row.paid_on = data.paid_on
        await self._post(row, data.paid_on, "PAYABLE", row.payment_mode, f"Pay {row.voucher_no}")
        await self.db.flush()
        await write_audit(
            self.db,
            school_id=self.school_id,
            actor_id=self.user_id,
            action="finance.expense.pay",
            resource_type="school_expense",
            resource_id=str(row.id),
            message=f"Expense {row.voucher_no} paid",
            details={"paid_on": str(data.paid_on), "mode": row.payment_mode},
        )
        return _expense_out(row)


async def expense_day_book(
    db: AsyncSession, school_id: UUID, on: date, branch_id: UUID | None
) -> list[dict]:
    """Paid expenses hit the day book on the payment date. Unpaid approvals hit the invoice date."""
    stmt = select(SchoolExpense).where(SchoolExpense.school_id == school_id)
    if branch_id:
        stmt = stmt.where(SchoolExpense.branch_id == branch_id)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for row in rows:
        show = (row.status == "PAID" and row.paid_on == on) or (
            row.status == "APPROVED" and row.paid_on is None and row.invoice_date == on
        )
        if not show:
            continue
        out.append(
            {
                "receipt": row.voucher_no,
                "status": row.status,
                "student": row.vendor_name,
                "amount": str(row.total_amount),
                "method": row.payment_mode,
                "collector": "",
                "time": (row.paid_on or row.invoice_date).isoformat(),
                "kind": "expense",
            }
        )
    return out


Svc = Annotated[FinanceService, Depends(_svc)]


@router.get("/expense-heads")
async def list_heads(svc: Svc):
    assert_finance(svc.role, "reports.read")
    return await ExpenseService(svc).list_heads()


@router.post("/expense-heads", status_code=201)
async def create_head(body: HeadIn, svc: Svc):
    assert_finance(svc.role, "expense.write")
    return await ExpenseService(svc).create_head(body)


@router.get("/expenses")
async def list_expenses(svc: Svc, status: str | None = Query(default=None)):
    assert_finance(svc.role, "reports.read")
    return await ExpenseService(svc).list_expenses(status)


@router.post("/expenses", status_code=201)
async def create_expense(body: ExpenseIn, svc: Svc):
    assert_finance(svc.role, "expense.write")
    return await ExpenseService(svc).create_expense(body)


@router.post("/expenses/{expense_id}/submit")
async def submit_expense(expense_id: UUID, svc: Svc):
    assert_finance(svc.role, "expense.write")
    return await ExpenseService(svc).submit(expense_id)


@router.post("/expenses/{expense_id}/approve")
async def approve_expense(expense_id: UUID, body: DecisionIn, svc: Svc):
    assert_finance(svc.role, "expense.approve")
    return await ExpenseService(svc).decide(expense_id, True, body.note)


@router.post("/expenses/{expense_id}/reject")
async def reject_expense(expense_id: UUID, body: DecisionIn, svc: Svc):
    assert_finance(svc.role, "expense.approve")
    return await ExpenseService(svc).decide(expense_id, False, body.note)


@router.post("/expenses/{expense_id}/pay")
async def pay_expense(expense_id: UUID, body: PayIn, svc: Svc):
    assert_finance(svc.role, "expense.write")
    return await ExpenseService(svc).pay(expense_id, body)
