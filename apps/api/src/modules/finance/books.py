"""School books. Amounts are rupees. A posted journal must balance and is not edited."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, NotFoundError
from src.db.models.books import (
    BookAsset,
    BookBankAccount,
    BookBankLine,
    BookRefund,
    CautionDeposit,
    GstInvoice,
    TdsEntry,
    BookPurchase,
    BookVendor,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    SchoolBudget,
    SchoolFund,
    TaxItem,
)
from src.db.models.finance import FeeLedgerEntry
from src.modules.finance.permissions import assert_finance
from src.modules.finance.router import _svc
from src.modules.finance.service import FinanceService

router = APIRouter(prefix="/finance", tags=["finance-books"])

DEFAULT_ACCOUNTS = (
    ("1000", "Cash", "ASSET"),
    ("1010", "Bank", "ASSET"),
    ("1100", "Fee receivable", "ASSET"),
    ("2000", "Vendors", "LIABILITY"),
    ("4000", "Tuition fees", "INCOME"),
    ("4010", "Admission fees", "INCOME"),
    ("4020", "Transport fees", "INCOME"),
    ("4900", "Other income", "INCOME"),
    ("5000", "Fee concession", "EXPENSE"),
    ("5100", "Salary", "EXPENSE"),
    ("5200", "Purchases", "EXPENSE"),
)


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def balanced(lines: list[tuple[Decimal, Decimal]]) -> bool:
    debit = sum((_money(d) for d, _c in lines), Decimal("0"))
    credit = sum((_money(c) for _d, c in lines), Decimal("0"))
    return debit == credit and debit > 0


class LineIn(BaseModel):
    account_code: str = Field(min_length=2, max_length=32)
    debit: Decimal = Field(default=Decimal("0"), ge=0)
    credit: Decimal = Field(default=Decimal("0"), ge=0)
    narration: str | None = None


class JournalIn(BaseModel):
    entry_date: date
    narration: str = Field(min_length=2, max_length=500)
    reference: str | None = None
    lines: list[LineIn] = Field(min_length=2)
    post: bool = True


class FundIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=160)
    opening_balance: Decimal = Decimal("0")


class VendorIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    gstin: str | None = None
    phone: str | None = None


class BudgetIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    year_label: str = Field(min_length=4, max_length=16)
    amount: Decimal = Field(gt=0)
    account_code: str | None = None


class BankIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    bank_name: str = Field(min_length=2, max_length=160)
    account_last4: str | None = Field(default=None, max_length=4)
    ifsc: str | None = None
    opening_balance: Decimal = Decimal("0")


class BankLineIn(BaseModel):
    bank_account_id: UUID
    line_date: date
    description: str = Field(min_length=1, max_length=500)
    debit: Decimal = Field(default=Decimal("0"), ge=0)
    credit: Decimal = Field(default=Decimal("0"), ge=0)
    reference: str | None = Field(default=None, max_length=64)
    balance: Decimal | None = None

    @model_validator(mode="after")
    def one_side(self):
        if self.debit > 0 and self.credit > 0:
            raise ValueError("A statement line cannot be both a debit and a credit")
        if self.debit == 0 and self.credit == 0:
            raise ValueError("A statement line needs a debit or a credit")
        return self


class BooksSettingsIn(BaseModel):
    financial_year_start_month: int = Field(ge=1, le=12)


class CautionIn(BaseModel):
    student_id: UUID
    amount: Decimal = Field(gt=0)
    received_on: date
    note: str | None = Field(default=None, max_length=240)


class BookRefundIn(BaseModel):
    student_id: UUID | None = None
    amount: Decimal = Field(gt=0)
    refund_date: date
    reason: str = Field(min_length=2, max_length=240)
    status: str = "REQUESTED"


class TdsIn(BaseModel):
    party_name: str = Field(min_length=2, max_length=200)
    pan: str | None = Field(default=None, max_length=10)
    section_code: str = Field(default="194C", max_length=16)
    amount: Decimal = Field(gt=0)
    deducted_on: date
    note: str | None = None


class GstInvoiceIn(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=40)
    party_name: str = Field(min_length=2, max_length=200)
    gstin: str | None = Field(default=None, max_length=15)
    invoice_date: date
    taxable_amount: Decimal = Field(ge=0)
    gst_amount: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None


class TaxIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=160)
    rate: Decimal = Field(ge=0, le=100)
    kind: str = "GST"


class PurchaseIn(BaseModel):
    vendor_id: UUID | None = None
    purchase_date: date
    amount: Decimal = Field(gt=0)
    narration: str | None = None
    approve: bool = False


class AssetIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    acquired_on: date | None = None
    cost: Decimal = Field(default=Decimal("0"), ge=0)
    location: str | None = None


def _row(obj) -> dict:
    data = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, (date, UUID)):
            value = str(value)
        data[column.name] = value
    return data


class Books:
    def __init__(self, db: AsyncSession, school_id: UUID, user_id: UUID):
        self.db = db
        self.school_id = school_id
        self.user_id = user_id

    async def ensure_chart(self) -> None:
        existing = (
            await self.db.execute(select(LedgerAccount.id).where(LedgerAccount.school_id == self.school_id).limit(1))
        ).first()
        if existing:
            return
        for code, name, kind in DEFAULT_ACCOUNTS:
            self.db.add(
                LedgerAccount(id=uuid4(), school_id=self.school_id, code=code, name=name, account_type=kind, active=True)
            )
        await self.db.flush()

    async def _account(self, code: str) -> LedgerAccount:
        await self.ensure_chart()
        row = (
            await self.db.execute(
                select(LedgerAccount).where(
                    LedgerAccount.school_id == self.school_id, LedgerAccount.code == code.strip().upper()
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"Ledger account {code} not found")
        return row

    async def accounts(self) -> list[dict]:
        await self.ensure_chart()
        rows = (
            await self.db.execute(
                select(LedgerAccount).where(LedgerAccount.school_id == self.school_id).order_by(LedgerAccount.code)
            )
        ).scalars().all()
        return [_row(r) for r in rows]

    async def create_journal(self, data: JournalIn) -> dict:
        pairs = [(_money(line.debit), _money(line.credit)) for line in data.lines]
        if not balanced(pairs):
            raise AppError("A journal must balance: debits must equal credits and be more than zero", status_code=422)
        for line, (debit, credit) in zip(data.lines, pairs, strict=True):
            if debit > 0 and credit > 0:
                raise AppError("A line cannot be both a debit and a credit", status_code=422)
            if debit == 0 and credit == 0:
                raise AppError("A line needs a debit or a credit", status_code=422)
        entry = JournalEntry(
            id=uuid4(),
            school_id=self.school_id,
            entry_date=data.entry_date,
            narration=data.narration.strip(),
            status="POSTED" if data.post else "DRAFT",
            reference=data.reference,
            created_by=self.user_id,
        )
        self.db.add(entry)
        await self.db.flush()
        for line, (debit, credit) in zip(data.lines, pairs, strict=True):
            account = await self._account(line.account_code)
            self.db.add(
                JournalLine(
                    id=uuid4(),
                    school_id=self.school_id,
                    entry_id=entry.id,
                    account_id=account.id,
                    debit=debit,
                    credit=credit,
                    narration=line.narration,
                )
            )
        await self.db.flush()
        return await self.journal(entry.id)

    async def journals(self) -> list[dict]:
        rows = (
            await self.db.execute(
                select(JournalEntry)
                .where(JournalEntry.school_id == self.school_id)
                .order_by(JournalEntry.entry_date.desc())
                .limit(200)
            )
        ).scalars().all()
        return [_row(r) for r in rows]

    async def journal(self, entry_id: UUID) -> dict:
        entry = await self.db.get(JournalEntry, entry_id)
        if entry is None or entry.school_id != self.school_id:
            raise NotFoundError("Journal not found")
        lines = (
            await self.db.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))
        ).scalars().all()
        body = _row(entry)
        body["lines"] = [_row(line) for line in lines]
        return body

    async def transactions(self) -> list[dict]:
        """Posted journal lines, the books view of money that has been recorded."""
        rows = (
            await self.db.execute(
                select(JournalLine, JournalEntry, LedgerAccount)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .join(LedgerAccount, LedgerAccount.id == JournalLine.account_id)
                .where(JournalLine.school_id == self.school_id, JournalEntry.status == "POSTED")
                .order_by(JournalEntry.entry_date.desc())
                .limit(300)
            )
        ).all()
        return [
            {
                "date": str(entry.entry_date),
                "account_code": account.code,
                "account_name": account.name,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "narration": line.narration or entry.narration,
                "journal_id": str(entry.id),
            }
            for line, entry, account in rows
        ]

    async def fee_ledger(self) -> list[dict]:
        rows = (
            await self.db.execute(
                select(FeeLedgerEntry)
                .where(FeeLedgerEntry.school_id == self.school_id)
                .order_by(FeeLedgerEntry.entry_date.desc())
                .limit(300)
            )
        ).scalars().all()
        return [_row(r) for r in rows]

    async def _list(self, model, order):
        rows = (await self.db.execute(select(model).where(model.school_id == self.school_id).order_by(order).limit(200))).scalars().all()
        return [_row(r) for r in rows]

    async def funds(self) -> list[dict]:
        return await self._list(SchoolFund, SchoolFund.name)

    async def add_fund(self, data: FundIn) -> dict:
        row = SchoolFund(id=uuid4(), school_id=self.school_id, code=data.code.strip().upper(), name=data.name.strip(), opening_balance=_money(data.opening_balance))
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def vendors(self) -> list[dict]:
        return await self._list(BookVendor, BookVendor.name)

    async def add_vendor(self, data: VendorIn) -> dict:
        row = BookVendor(id=uuid4(), school_id=self.school_id, name=data.name.strip(), gstin=(data.gstin or "").strip().upper() or None, phone=data.phone, active=True)
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def budgets(self) -> list[dict]:
        return await self._list(SchoolBudget, SchoolBudget.year_label)

    async def add_budget(self, data: BudgetIn) -> dict:
        row = SchoolBudget(id=uuid4(), school_id=self.school_id, name=data.name.strip(), year_label=data.year_label.strip(), amount=_money(data.amount), account_code=data.account_code)
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def banks(self) -> list[dict]:
        return await self._list(BookBankAccount, BookBankAccount.name)

    async def bank_lines(self, bank_account_id: UUID | None) -> list[dict]:
        stmt = select(BookBankLine).where(BookBankLine.school_id == self.school_id)
        if bank_account_id is not None:
            stmt = stmt.where(BookBankLine.bank_account_id == bank_account_id)
        rows = (await self.db.execute(stmt.order_by(BookBankLine.line_date.desc()).limit(300))).scalars().all()
        return [_row(row) for row in rows]

    async def add_bank_line(self, data: BankLineIn) -> dict:
        account = await self.db.get(BookBankAccount, data.bank_account_id)
        if account is None or account.school_id != self.school_id:
            raise NotFoundError("Bank account not found")
        row = BookBankLine(
            id=uuid4(),
            school_id=self.school_id,
            bank_account_id=account.id,
            line_date=data.line_date,
            description=data.description.strip(),
            debit=_money(data.debit),
            credit=_money(data.credit),
            reference=(data.reference or "").strip() or None,
            balance=_money(data.balance) if data.balance is not None else None,
        )
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def caution_deposits(self) -> list[dict]:
        return await self._list(CautionDeposit, CautionDeposit.received_on.desc())

    async def add_caution(self, data: CautionIn) -> dict:
        row = CautionDeposit(
            id=uuid4(), school_id=self.school_id, student_id=data.student_id, amount=_money(data.amount),
            received_on=data.received_on, status="HELD", note=data.note,
        )
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def book_refunds(self) -> list[dict]:
        return await self._list(BookRefund, BookRefund.refund_date.desc())

    async def add_book_refund(self, data: BookRefundIn) -> dict:
        if data.status not in {"REQUESTED", "PAID"}:
            raise AppError("Status must be REQUESTED or PAID", status_code=422)
        row = BookRefund(
            id=uuid4(), school_id=self.school_id, student_id=data.student_id, amount=_money(data.amount),
            refund_date=data.refund_date, reason=data.reason.strip(), status=data.status,
        )
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def tds_entries(self) -> list[dict]:
        return await self._list(TdsEntry, TdsEntry.deducted_on.desc())

    async def add_tds(self, data: TdsIn) -> dict:
        row = TdsEntry(
            id=uuid4(), school_id=self.school_id, party_name=data.party_name.strip(),
            pan=(data.pan or "").strip().upper() or None, section_code=data.section_code.strip().upper(),
            amount=_money(data.amount), deducted_on=data.deducted_on, note=data.note,
        )
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def gst_invoices(self) -> list[dict]:
        return await self._list(GstInvoice, GstInvoice.invoice_date.desc())

    async def add_gst_invoice(self, data: GstInvoiceIn) -> dict:
        total = _money(data.taxable_amount + data.gst_amount)
        row = GstInvoice(
            id=uuid4(), school_id=self.school_id, invoice_number=data.invoice_number.strip(),
            party_name=data.party_name.strip(), gstin=(data.gstin or "").strip().upper() or None,
            invoice_date=data.invoice_date, taxable_amount=_money(data.taxable_amount),
            gst_amount=_money(data.gst_amount), total=total, note=data.note,
        )
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def add_bank(self, data: BankIn) -> dict:
        row = BookBankAccount(
            id=uuid4(), school_id=self.school_id, name=data.name.strip(), bank_name=data.bank_name.strip(),
            account_last4=data.account_last4, ifsc=(data.ifsc or "").strip().upper() or None,
            opening_balance=_money(data.opening_balance), active=True,
        )
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def taxes(self) -> list[dict]:
        return await self._list(TaxItem, TaxItem.code)

    async def add_tax(self, data: TaxIn) -> dict:
        row = TaxItem(id=uuid4(), school_id=self.school_id, code=data.code.strip().upper(), name=data.name.strip(), rate=_money(data.rate), kind=data.kind.strip().upper())
        self.db.add(row)
        await self.db.flush()
        return _row(row)

    async def purchases(self) -> list[dict]:
        return await self._list(BookPurchase, BookPurchase.purchase_date.desc())

    async def add_purchase(self, data: PurchaseIn) -> dict:
        row = BookPurchase(
            id=uuid4(), school_id=self.school_id, vendor_id=data.vendor_id, purchase_date=data.purchase_date,
            amount=_money(data.amount), narration=data.narration, status="DRAFT",
        )
        self.db.add(row)
        await self.db.flush()
        if data.approve:
            journal = await self.create_journal(
                JournalIn(
                    entry_date=data.purchase_date,
                    narration=data.narration or "Purchase",
                    reference=str(row.id),
                    lines=[
                        LineIn(account_code="5200", debit=data.amount),
                        LineIn(account_code="2000", credit=data.amount),
                    ],
                    post=True,
                )
            )
            row.status = "APPROVED"
            row.journal_entry_id = UUID(journal["id"])
            await self.db.flush()
        return _row(row)

    async def assets(self) -> list[dict]:
        return await self._list(BookAsset, BookAsset.name)

    async def add_asset(self, data: AssetIn) -> dict:
        row = BookAsset(id=uuid4(), school_id=self.school_id, name=data.name.strip(), acquired_on=data.acquired_on, cost=_money(data.cost), location=data.location, status="IN_USE")
        self.db.add(row)
        await self.db.flush()
        return _row(row)


def _books(svc: FinanceService) -> Books:
    return Books(svc.db, svc.school_id, svc.user_id)


@router.get("/accounts")
async def list_accounts(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).accounts()


@router.get("/journals")
async def list_journals(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).journals()


@router.post("/journals", status_code=201)
async def create_journal(body: JournalIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).create_journal(body)


@router.get("/transactions")
async def list_transactions(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).transactions()


@router.get("/fee-ledger")
async def fee_ledger(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).fee_ledger()


@router.get("/funds")
async def list_funds(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).funds()


@router.post("/funds", status_code=201)
async def create_fund(body: FundIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_fund(body)


@router.get("/vendors")
async def list_vendors(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).vendors()


@router.post("/vendors", status_code=201)
async def create_vendor(body: VendorIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_vendor(body)


@router.get("/budgets")
async def list_budgets(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).budgets()


@router.post("/budgets", status_code=201)
async def create_budget(body: BudgetIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_budget(body)


@router.get("/bank-accounts")
async def list_banks(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).banks()


@router.post("/bank-accounts", status_code=201)
async def create_bank(body: BankIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_bank(body)


@router.get("/bank-lines")
async def list_bank_lines(
    svc: Annotated[FinanceService, Depends(_svc)],
    bank_account_id: UUID | None = None,
):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).bank_lines(bank_account_id)


@router.post("/bank-lines", status_code=201)
async def create_bank_line(body: BankLineIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_bank_line(body)


@router.get("/caution-deposits")
async def list_caution(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).caution_deposits()


@router.post("/caution-deposits", status_code=201)
async def create_caution(body: CautionIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_caution(body)


@router.get("/book-refunds")
async def list_book_refunds(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).book_refunds()


@router.post("/book-refunds", status_code=201)
async def create_book_refund(body: BookRefundIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_book_refund(body)


@router.get("/tds-entries")
async def list_tds(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).tds_entries()


@router.post("/tds-entries", status_code=201)
async def create_tds(body: TdsIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_tds(body)


@router.get("/gst-invoices")
async def list_gst(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).gst_invoices()


@router.post("/gst-invoices", status_code=201)
async def create_gst(body: GstInvoiceIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_gst_invoice(body)


@router.get("/settings")
async def books_settings(svc: Annotated[FinanceService, Depends(_svc)]):
    """Default ledger accounts and the school financial year. The year is the fee setting."""
    assert_finance(svc.role, "ledger.read")
    from src.modules.fees.exports import fee_books_view, financial_year_label
    from src.modules.fees.service import FeeService

    fee = await FeeService(svc.db).get_or_create_settings(svc.school_id)
    view = fee_books_view(fee)
    years = await svc.list_years()
    return {
        "financial_year_start_month": view["financial_year_start_month"],
        "financial_year": financial_year_label(date.today(), view["financial_year_start_month"]),
        "default_accounts": [{"code": code, "name": name, "kind": kind} for code, name, kind in DEFAULT_ACCOUNTS],
        "years": [year.model_dump(mode="json") for year in years],
    }


@router.patch("/settings")
async def update_books_settings(body: BooksSettingsIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    from src.modules.fees.schemas import FeeSettingsUpdate
    from src.modules.fees.service import FeeService

    await FeeService(svc.db).update_settings(
        svc.school_id, FeeSettingsUpdate(financial_year_start_month=body.financial_year_start_month)
    )
    return await books_settings(svc)


@router.get("/tax-items")
async def list_taxes(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).taxes()


@router.post("/tax-items", status_code=201)
async def create_tax(body: TaxIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_tax(body)


@router.get("/purchases")
async def list_purchases(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).purchases()


@router.post("/purchases", status_code=201)
async def create_purchase(body: PurchaseIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_purchase(body)


@router.get("/assets")
async def list_assets(svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "ledger.read")
    return await _books(svc).assets()


@router.post("/assets", status_code=201)
async def create_asset(body: AssetIn, svc: Annotated[FinanceService, Depends(_svc)]):
    assert_finance(svc.role, "expense.approve")
    return await _books(svc).add_asset(body)
