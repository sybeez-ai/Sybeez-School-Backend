"""Tally XML and section 80C certificates. Wording and ledger names are school settings."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models.fee import FeeCategory, FeeInvoice, FeeInvoiceItem, Payment, StudentFeeAccount
from src.db.models.student import Student
from src.db.models.user import User
from src.modules.fees.calculations import money
from src.modules.fees.service import FeeService
from src.shared.enums import FeeInvoiceStatus, PaymentStatus

DEFAULT_TALLY_MAP = {
    "TUITION": "Tuition Fees",
    "ADMISSION": "Admission Fees",
    "TRANSPORT": "Transport Fees",
    "EXAM": "Examination Fees",
    "BOOKS": "Book Sales",
    "CASH": "Cash",
    "BANK": "Bank",
    "UPI": "Bank",
    "ONLINE": "Bank",
    "CONCESSION": "Fee Concession",
    "OTHER": "Other Fees",
}

DEFAULT_80C = (
    "This is to certify that {parent_name}, parent/guardian of {student_name} "
    "(admission {admission_no}), paid tuition fees of Rs {amount} to {school_name} "
    "during the financial year {financial_year}. Issued for section 80C of the Income-tax Act, 1961."
)


def financial_year_label(day: date, start_month: int = 4) -> str:
    """Indian default is April–March. start_month 4 means FY 2026-27 for a date in May 2026."""
    start_month = start_month if 1 <= start_month <= 12 else 4
    if day.month >= start_month:
        start_year = day.year
    else:
        start_year = day.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def fee_books_view(settings) -> dict:
    """What the fee settings screen should show. Blank columns use the built-in defaults."""
    stored = getattr(settings, "tally_ledger_map", None) or {}
    cleaned = {str(key).upper(): str(value) for key, value in stored.items() if value}
    template = (getattr(settings, "section_80c_template", None) or "").strip()
    signatory = (getattr(settings, "section_80c_signatory", None) or "").strip() or "Principal"
    heads = [str(head).strip().upper() for head in (getattr(settings, "section_80c_heads", None) or []) if str(head).strip()]
    month = int(getattr(settings, "financial_year_start_month", None) or 4)
    if not 1 <= month <= 12:
        month = 4
    return {
        "tally_ledger_map": {**DEFAULT_TALLY_MAP, **cleaned},
        "section_80c_template": template or DEFAULT_80C,
        "section_80c_signatory": signatory,
        "section_80c_heads": heads or ["TUITION"],
        "financial_year_start_month": month,
    }


def ledger_name(mapping: dict | None, code: str) -> str:
    table = {**DEFAULT_TALLY_MAP, **{str(k).upper(): str(v) for k, v in (mapping or {}).items() if v}}
    key = (code or "OTHER").upper()
    return table.get(key) or table["OTHER"]


def tally_voucher_xml(*, day: date, number: str, debit_ledger: str, credit_ledger: str, amount: Decimal) -> str:
    rupees = f"{money(amount):.2f}"
    return (
        "<TALLYMESSAGE>"
        "<VOUCHER VCHTYPE=\"Receipt\" ACTION=\"Create\">"
        f"<DATE>{day.strftime('%Y%m%d')}</DATE>"
        f"<VOUCHERNUMBER>{escape(number)}</VOUCHERNUMBER>"
        f"<NARRATION>Fee receipt {escape(number)}</NARRATION>"
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{escape(debit_ledger)}</LEDGERNAME>"
        f"<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{rupees}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{escape(credit_ledger)}</LEDGERNAME>"
        f"<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{rupees}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
        "</VOUCHER></TALLYMESSAGE>"
    )


def tally_envelope(vouchers: list[str]) -> str:
    body = "".join(vouchers)
    return (
        "<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
        f"<BODY><IMPORTDATA><REQUESTDATA>{body}</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
    )


def render_80c(template: str, values: dict[str, str]) -> str:
    text = template.strip() or DEFAULT_80C
    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return ""

    return text.format_map(_Safe(values))


async def build_tally_xml(db: AsyncSession, school_id: UUID) -> str:
    settings = await FeeService(db).get_or_create_settings(school_id)
    mapping = settings.tally_ledger_map or {}
    payments = (
        await db.execute(
            select(Payment)
            .where(Payment.school_id == school_id, Payment.status == PaymentStatus.SUCCESS.value)
            .order_by(Payment.paid_at.desc())
            .limit(500)
        )
    ).scalars().all()
    vouchers = []
    for payment in payments:
        day = (payment.paid_at or payment.created_at).date()
        method = (payment.method or "CASH").upper()
        debit = ledger_name(mapping, "CASH" if method == "CASH" else "BANK")
        credit = ledger_name(mapping, "TUITION")
        vouchers.append(
            tally_voucher_xml(
                day=day,
                number=payment.payment_number or str(payment.id)[:8],
                debit_ledger=debit,
                credit_ledger=credit,
                amount=payment.amount,
            )
        )
    return tally_envelope(vouchers)


async def build_80c(db: AsyncSession, school_id: UUID, student_id: UUID, school_name: str) -> dict:
    settings = await FeeService(db).get_or_create_settings(school_id)
    student = await db.get(Student, student_id)
    if student is None or student.school_id != school_id:
        raise NotFoundError("Student not found")
    user = await db.get(User, student.user_id)
    heads = {str(h).upper() for h in (settings.section_80c_heads or ["TUITION"])}
    invoices = (
        await db.execute(
            select(FeeInvoice)
            .join(StudentFeeAccount, StudentFeeAccount.id == FeeInvoice.student_fee_account_id)
            .where(
                StudentFeeAccount.student_id == student_id,
                FeeInvoice.school_id == school_id,
                FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
            )
        )
    ).scalars().all()
    total = Decimal("0")
    for invoice in invoices:
        items = (
            await db.execute(
                select(FeeInvoiceItem, FeeCategory)
                .join(FeeCategory, FeeCategory.id == FeeInvoiceItem.fee_category_id, isouter=True)
                .where(FeeInvoiceItem.invoice_id == invoice.id)
            )
        ).all()
        eligible = False
        if not items and "TUITION" in heads:
            eligible = True
        for _item, category in items:
            code = (category.code if category else "").upper()
            if category and category.is_80c_eligible:
                eligible = True
            elif code and (code in heads or any(code.startswith(h) for h in heads)):
                eligible = True
        if eligible:
            total += money(invoice.paid_amount)
    start_month = settings.financial_year_start_month or 4
    fy = financial_year_label(date.today(), start_month)
    body = render_80c(
        settings.section_80c_template or "",
        {
            "parent_name": "Parent/Guardian",
            "student_name": user.full_name if user else "Student",
            "admission_no": student.admission_no or "",
            "amount": f"{money(total):.2f}",
            "school_name": school_name,
            "financial_year": fy,
            "signatory": settings.section_80c_signatory or "Principal",
        },
    )
    return {
        "student_id": str(student_id),
        "financial_year": fy,
        "amount": str(money(total)),
        "signatory": settings.section_80c_signatory or "Principal",
        "text": body + f"\n\n{settings.section_80c_signatory or 'Principal'}",
    }
