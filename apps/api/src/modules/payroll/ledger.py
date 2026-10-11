"""Post a released pay run to the finance expense ledger as one balanced salary voucher."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ExpenseHead, ExpenseLedgerEntry, SchoolExpense

ZERO = Decimal("0.00")
SALARY_HEAD = "SALARY"

# Payslip deduction codes and the payable account they are credited to.
DEDUCTION_ACCOUNTS = {
    "PF": "PF_PAYABLE",
    "VPF": "PF_PAYABLE",
    "ESI": "ESI_PAYABLE",
    "PT": "PT_PAYABLE",
    "TDS": "TDS_PAYABLE",
    "LOAN": "LOAN_RECOVERY",
}
EMPLOYER_ACCOUNTS = {
    "PF_EMPLOYER": "PF_PAYABLE",
    "ESI_EMPLOYER": "ESI_PAYABLE",
}


def salary_journal(slips: list[tuple[Decimal, list[tuple[str, str, Decimal]]]]) -> list[tuple[str, Decimal, Decimal]]:
    """Return balanced (account, debit, credit) lines for released payslips.

    Each slip is (net, [(code, kind, amount)]). Loss of pay reduces the expense
    rather than being credited anywhere. EPS is part of PF_EMPLOYER and is skipped.
    """
    credits: dict[str, Decimal] = {}
    expense = ZERO
    for net, lines in slips:
        credits["SALARY_PAYABLE"] = credits.get("SALARY_PAYABLE", ZERO) + Decimal(net)
        for code, kind, amount in lines:
            amount = Decimal(amount)
            code = code.upper()
            if kind == "EARNING":
                expense += amount
            elif kind == "DEDUCTION":
                if code == "LOP":
                    expense -= amount
                    continue
                account = DEDUCTION_ACCOUNTS.get(code, "OTHER_DEDUCTIONS_PAYABLE")
                credits[account] = credits.get(account, ZERO) + amount
            elif kind == "EMPLOYER" and code in EMPLOYER_ACCOUNTS:
                expense += amount
                account = EMPLOYER_ACCOUNTS[code]
                credits[account] = credits.get(account, ZERO) + amount
    total_credit = sum(credits.values(), ZERO)
    # Net is floored at zero on a slip; any shortfall is a recovery the employee still owes.
    if total_credit != expense:
        diff = expense - total_credit
        credits["SALARY_PAYABLE"] = credits.get("SALARY_PAYABLE", ZERO) + diff
    out = [("SALARY_EXPENSE", expense.quantize(Decimal("0.01")), ZERO)]
    for account in sorted(credits):
        amount = credits[account].quantize(Decimal("0.01"))
        if amount:
            out.append((account, ZERO, amount))
    return out


async def post_salary_voucher(
    db: AsyncSession,
    *,
    school_id: UUID,
    run_id: UUID,
    label: str,
    entry_date,
    actor_id: UUID | None,
    journal: list[tuple[str, Decimal, Decimal]],
) -> SchoolExpense | None:
    """Idempotent: a run posts once, keyed by its id in the invoice number."""
    invoice = f"PAYRUN-{run_id}"
    existing = (
        await db.execute(
            select(SchoolExpense).where(SchoolExpense.school_id == school_id, SchoolExpense.invoice_number == invoice)
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    total = sum((debit for _, debit, _ in journal), ZERO)
    if total <= 0:
        return None
    head = (
        await db.execute(
            select(ExpenseHead).where(ExpenseHead.school_id == school_id, func.lower(ExpenseHead.code) == "salary")
        )
    ).scalar_one_or_none()
    if head is None:
        head = ExpenseHead(school_id=school_id, code=SALARY_HEAD, name="Staff salaries", is_active=True)
        db.add(head)
        await db.flush()
    now = datetime.now(timezone.utc)
    row = SchoolExpense(
        school_id=school_id,
        head_id=head.id,
        voucher_no=f"SAL-{run_id.hex[:12].upper()}",
        vendor_name=f"Staff salaries {label}"[:200],
        invoice_number=invoice,
        invoice_date=entry_date,
        taxable_amount=total,
        gst_rate=ZERO,
        gst_amount=ZERO,
        total_amount=total,
        payment_mode="BANK",
        status="APPROVED",
        narration=f"Payroll {label}: salary expense and statutory payables",
        requested_by=actor_id,
        approved_by=actor_id,
        decided_at=now,
    )
    db.add(row)
    await db.flush()
    for account, debit, credit in journal:
        db.add(
            ExpenseLedgerEntry(
                school_id=school_id,
                expense_id=row.id,
                entry_date=entry_date,
                account=account,
                debit=debit,
                credit=credit,
                narration=f"Payroll {label}",
                actor_user_id=actor_id,
                created_at=now,
            )
        )
    await db.flush()
    return row
