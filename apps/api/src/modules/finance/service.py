"""Finance portal services. Every query filters by school_id; branch scope is applied when assigned."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, nulls_last, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    FeeAuditLog,
    FeeCategory,
    FeeInvoice,
    FeeInvoiceItem,
    FeeReceipt,
    FeeReminderLog,
    Payment,
    PaymentAllocation,
    SchoolMembership,
    Student,
    StudentFeeAccount,
    User,
)
from src.db.models.finance import (
    FeeConcessionRequest,
    FeeConcessionRule,
    FeeDayClose,
    FeeLedgerEntry,
    FeePaymentTender,
    FinanceApproval,
    FinanceStaffScope,
    FinancialYear,
    FineWaiverRequest,
    SchoolBranch,
    SiblingGroup,
    StudentFeeProfile,
)
from src.modules.fees.calculations import invoice_status_after_payment, money
from src.modules.fees.crediting import assert_distinct_checker
from src.modules.fees.ledger import ensure_ledger_backfill, post_ledger_entry
from src.modules.fees.numbering import indian_financial_year
from src.modules.fees.payment_service import PaymentService
from src.modules.fees.reversal import reverse_payment_amount
from src.modules.fees.schemas import ReceiptOut, RefundIn
from src.modules.fees.service import FeeService
from src.modules.fees.tenant import assert_same_school, bind_tenant
from src.modules.finance.schemas import (
    AllocationOut,
    ApprovalOut,
    AuditRow,
    BranchIn,
    BranchOut,
    BranchPatch,
    BounceIn,
    CancelReceiptIn,
    ChequeOut,
    CollectIn,
    CollectionOut,
    ConcessionOut,
    ConcessionRequestIn,
    ConcessionRuleIn,
    ConcessionRuleOut,
    ConcessionRulePatch,
    DashboardOut,
    DayCloseIn,
    DayCloseOut,
    DueLine,
    FeeProfileIn,
    FeeProfileOut,
    FinancialYearIn,
    FinancialYearOut,
    FineWaiverIn,
    LedgerRow,
    ReminderLogOut,
    SiblingGroupIn,
    SiblingGroupOut,
    StaffScopeIn,
    StaffScopeOut,
    StudentSearchHit,
    TenderOut,
)
from src.shared.enums import (
    ApprovalStatus,
    ConcessionValueType,
    DayCloseStatus,
    FeeAdjustmentType,
    FeeInvoiceStatus,
    FinanceApprovalType,
    FinancialYearStatus,
    InstrumentStatus,
    LedgerEntryType,
    MembershipStatus,
    PaymentMethod,
    PaymentStatus,
    ReceiptStatus,
    StudentFeeCategory,
    UserRole,
)
from src.db.models.fee import FeeAdjustment

CHEQUE_METHODS = {PaymentMethod.CHEQUE.value, PaymentMethod.DD.value}
MODE_BUCKETS = {
    PaymentMethod.CASH.value: "cash",
    PaymentMethod.CHEQUE.value: "cheque",
    PaymentMethod.DD.value: "cheque",
    PaymentMethod.UPI.value: "upi",
    PaymentMethod.CARD.value: "card",
    PaymentMethod.NEFT.value: "neft",
    PaymentMethod.BANK_TRANSFER.value: "neft",
    PaymentMethod.ONLINE.value: "online",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FinanceService:
    def __init__(self, db: AsyncSession, school_id: UUID, user_id: UUID, role: str):
        self.db = db
        self.school_id = school_id
        self.user_id = user_id
        self.role = role
        self.branch_ids: set[UUID] | None = None
        self.active_branch_id: UUID | None = None
        self.fees = FeeService(db)
        self.payments = PaymentService(db)

    async def prepare(self, branch_id: UUID | None = None) -> "FinanceService":
        await bind_tenant(self.db, self.school_id)
        if self.role == UserRole.SCHOOL_ADMIN.value:
            self.branch_ids = None
        else:
            rows = (
                await self.db.execute(
                    select(FinanceStaffScope).where(
                        FinanceStaffScope.school_id == self.school_id,
                        FinanceStaffScope.user_id == self.user_id,
                    )
                )
            ).scalars().all()
            if not rows:
                self.branch_ids = None
            else:
                self.branch_ids = {r.branch_id for r in rows if r.branch_id is not None}
                if not self.branch_ids:
                    self.branch_ids = None
        if branch_id is not None:
            branch = assert_same_school(
                await self.db.get(SchoolBranch, branch_id), self.school_id, "Branch"
            )
            if self.branch_ids is not None and branch.id not in self.branch_ids:
                raise ForbiddenError("Branch is outside your assignment")
            self.branch_ids = {branch.id}
            self.active_branch_id = branch.id
        return self

    def _require_branch(self, branch_id: UUID | None) -> UUID | None:
        if self.branch_ids is None:
            return branch_id
        if branch_id is None:
            if len(self.branch_ids) == 1:
                return next(iter(self.branch_ids))
            raise AppError("branch_id is required for your assignment", status_code=422)
        if branch_id not in self.branch_ids:
            raise ForbiddenError("Branch is outside your assignment")
        return branch_id

    def _limit(self, query, column, branch_id: UUID | None):
        if self.branch_ids is not None:
            if branch_id and branch_id not in self.branch_ids:
                raise ForbiddenError("Branch is outside your assignment")
            if branch_id:
                return query.where(column == branch_id)
            return query.where(column.in_(self.branch_ids))
        if branch_id:
            return query.where(column == branch_id)
        return query

    async def _audit(self, action: str, entity_type: str, entity_id: UUID | None, **kwargs) -> None:
        await self.fees._audit(self.school_id, self.user_id, action, entity_type, entity_id, **kwargs)

    async def _student(self, student_id: UUID) -> Student:
        return assert_same_school(await self.db.get(Student, student_id), self.school_id, "Student")

    # ── branches / years / scopes ─────────────────────────────

    async def list_branches(self) -> list[BranchOut]:
        q = select(SchoolBranch).where(SchoolBranch.school_id == self.school_id).order_by(SchoolBranch.name)
        if self.branch_ids is not None:
            q = q.where(SchoolBranch.id.in_(self.branch_ids))
        rows = (await self.db.execute(q)).scalars().all()
        return [BranchOut.model_validate(r) for r in rows]

    async def create_branch(self, data: BranchIn) -> BranchOut:
        code = data.code.strip().upper()
        exists = (
            await self.db.execute(
                select(SchoolBranch).where(
                    SchoolBranch.school_id == self.school_id, SchoolBranch.code == code
                )
            )
        ).scalar_one_or_none()
        if exists:
            raise ConflictError("Branch code already exists")
        row = SchoolBranch(
            id=uuid4(),
            school_id=self.school_id,
            name=data.name.strip(),
            code=code,
            address=data.address,
            active=data.active,
        )
        self.db.add(row)
        await self._audit("BRANCH_CREATED", "school_branch", row.id, new_values={"code": code})
        await self.db.commit()
        await self.db.refresh(row)
        return BranchOut.model_validate(row)

    async def update_branch(self, branch_id: UUID, data: BranchPatch) -> BranchOut:
        row = assert_same_school(await self.db.get(SchoolBranch, branch_id), self.school_id, "Branch")
        if data.name is not None:
            row.name = data.name.strip()
        if data.address is not None:
            row.address = data.address
        if data.active is not None:
            row.active = data.active
        await self._audit("BRANCH_UPDATED", "school_branch", row.id)
        await self.db.commit()
        return BranchOut.model_validate(row)

    async def list_years(self) -> list[FinancialYearOut]:
        rows = (
            await self.db.execute(
                select(FinancialYear)
                .where(FinancialYear.school_id == self.school_id)
                .order_by(FinancialYear.start_date.desc())
            )
        ).scalars().all()
        return [FinancialYearOut.model_validate(r) for r in rows]

    async def create_year(self, data: FinancialYearIn) -> FinancialYearOut:
        if data.end_date <= data.start_date:
            raise AppError("Financial year end must be after start", status_code=422)
        row = FinancialYear(
            id=uuid4(),
            school_id=self.school_id,
            label=data.label.strip(),
            start_date=data.start_date,
            end_date=data.end_date,
            status=FinancialYearStatus.OPEN.value,
            is_current=data.is_current,
        )
        if data.is_current:
            await self._clear_current_year()
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError("Financial year label already exists")
        await self.db.refresh(row)
        return FinancialYearOut.model_validate(row)

    async def _clear_current_year(self) -> None:
        rows = (
            await self.db.execute(
                select(FinancialYear).where(
                    FinancialYear.school_id == self.school_id, FinancialYear.is_current.is_(True)
                )
            )
        ).scalars().all()
        for row in rows:
            row.is_current = False

    async def close_year(self, year_id: UUID) -> FinancialYearOut:
        row = assert_same_school(await self.db.get(FinancialYear, year_id), self.school_id, "Financial year")
        row.status = FinancialYearStatus.CLOSED.value
        row.is_current = False
        await self._audit("FY_CLOSED", "financial_year", row.id, new_values={"label": row.label})
        await self.db.commit()
        return FinancialYearOut.model_validate(row)

    async def _assert_fy_open(self, on: date) -> None:
        _start, label = indian_financial_year(on)
        row = (
            await self.db.execute(
                select(FinancialYear).where(
                    FinancialYear.school_id == self.school_id, FinancialYear.label == label
                )
            )
        ).scalar_one_or_none()
        if row and row.status == FinancialYearStatus.CLOSED.value:
            raise AppError(f"Financial year {label} is closed", status_code=422)

    async def list_scopes(self) -> list[StaffScopeOut]:
        rows = (
            await self.db.execute(
                select(FinanceStaffScope).where(FinanceStaffScope.school_id == self.school_id)
            )
        ).scalars().all()
        return [
            StaffScopeOut(id=r.id, school_id=r.school_id, user_id=r.user_id, branch_id=r.branch_id)
            for r in rows
        ]

    async def add_scope(self, data: StaffScopeIn) -> StaffScopeOut:
        membership = (
            await self.db.execute(
                select(SchoolMembership).where(
                    SchoolMembership.school_id == self.school_id,
                    SchoolMembership.user_id == data.user_id,
                    SchoolMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        if not membership:
            raise NotFoundError("User has no active membership in this school")
        branch = assert_same_school(
            await self.db.get(SchoolBranch, data.branch_id), self.school_id, "Branch"
        )
        row = FinanceStaffScope(
            id=uuid4(), school_id=self.school_id, user_id=data.user_id, branch_id=branch.id
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError("Scope already exists")
        await self.db.refresh(row)
        return StaffScopeOut(id=row.id, school_id=row.school_id, user_id=row.user_id, branch_id=row.branch_id)

    # ── students ──────────────────────────────────────────────

    async def search_students(self, q: str) -> list[StudentSearchHit]:
        term = q.strip().replace("%", "").replace("_", "")
        if len(term) < 1:
            return []
        like = f"%{term}%"
        rows = (
            await self.db.execute(
                select(Student, User.full_name)
                .join(User, User.id == Student.user_id)
                .where(
                    Student.school_id == self.school_id,
                    or_(
                        User.full_name.ilike(like),
                        Student.admission_no.ilike(like),
                        Student.phone.ilike(like),
                    ),
                )
                .limit(25)
            )
        ).all()
        out: list[StudentSearchHit] = []
        for student, name in rows:
            profile = await self._profile_row(student.id)
            if self.branch_ids is not None and profile and profile.branch_id not in self.branch_ids:
                continue
            outstanding = await self._outstanding(student.id)
            out.append(
                StudentSearchHit(
                    student_id=student.id,
                    full_name=name,
                    admission_no=student.admission_no,
                    phone=student.phone,
                    category=profile.category if profile else StudentFeeCategory.GENERAL.value,
                    branch_id=profile.branch_id if profile else None,
                    outstanding=outstanding,
                )
            )
        return out

    async def _profile_row(self, student_id: UUID) -> StudentFeeProfile | None:
        return (
            await self.db.execute(
                select(StudentFeeProfile).where(
                    StudentFeeProfile.school_id == self.school_id,
                    StudentFeeProfile.student_id == student_id,
                )
            )
        ).scalar_one_or_none()

    async def _outstanding(self, student_id: UUID) -> Decimal:
        accounts = (
            await self.db.execute(
                select(StudentFeeAccount.id).where(
                    StudentFeeAccount.school_id == self.school_id,
                    StudentFeeAccount.student_id == student_id,
                )
            )
        ).scalars().all()
        if not accounts:
            return money(0)
        total = (
            await self.db.execute(
                select(func.coalesce(func.sum(FeeInvoice.balance_amount), 0)).where(
                    FeeInvoice.school_id == self.school_id,
                    FeeInvoice.student_fee_account_id.in_(accounts),
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
            )
        ).scalar_one()
        return money(total)

    async def get_profile(self, student_id: UUID) -> FeeProfileOut:
        await self._student(student_id)
        row = await self._profile_row(student_id)
        if not row:
            return FeeProfileOut(
                student_id=student_id,
                school_id=self.school_id,
                category=StudentFeeCategory.GENERAL.value,
                transport_opt_in=False,
                hostel_opt_in=False,
            )
        return self._profile_out(row)

    def _profile_out(self, row: StudentFeeProfile) -> FeeProfileOut:
        return FeeProfileOut(
            student_id=row.student_id,
            school_id=row.school_id,
            category=row.category,
            branch_id=row.branch_id,
            sibling_group_id=row.sibling_group_id,
            transport_opt_in=row.transport_opt_in,
            hostel_opt_in=row.hostel_opt_in,
        )

    async def upsert_profile(self, student_id: UUID, data: FeeProfileIn) -> FeeProfileOut:
        await self._student(student_id)
        if data.category not in {c.value for c in StudentFeeCategory}:
            raise AppError("Unknown student fee category", status_code=422)
        branch_id = self._require_branch(data.branch_id) if data.branch_id or self.branch_ids else data.branch_id
        if data.sibling_group_id:
            assert_same_school(
                await self.db.get(SiblingGroup, data.sibling_group_id), self.school_id, "Sibling group"
            )
        row = await self._profile_row(student_id)
        if not row:
            row = StudentFeeProfile(id=uuid4(), school_id=self.school_id, student_id=student_id)
            self.db.add(row)
        row.category = data.category
        row.branch_id = branch_id
        row.sibling_group_id = data.sibling_group_id
        row.transport_opt_in = data.transport_opt_in
        row.hostel_opt_in = data.hostel_opt_in
        await self._audit("FEE_PROFILE_SET", "student_fee_profile", row.id, new_values={"category": data.category})
        await self.db.commit()
        await self.db.refresh(row)
        return self._profile_out(row)

    async def create_sibling_group(self, data: SiblingGroupIn) -> SiblingGroupOut:
        row = SiblingGroup(id=uuid4(), school_id=self.school_id, name=data.name.strip())
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return SiblingGroupOut(id=row.id, school_id=row.school_id, name=row.name)

    async def list_sibling_groups(self) -> list[SiblingGroupOut]:
        rows = (
            await self.db.execute(select(SiblingGroup).where(SiblingGroup.school_id == self.school_id))
        ).scalars().all()
        return [SiblingGroupOut(id=r.id, school_id=r.school_id, name=r.name) for r in rows]

    async def dues(self, student_id: UUID) -> list[DueLine]:
        await self._student(student_id)
        accounts = (
            await self.db.execute(
                select(StudentFeeAccount.id).where(
                    StudentFeeAccount.school_id == self.school_id,
                    StudentFeeAccount.student_id == student_id,
                )
            )
        ).scalars().all()
        if not accounts:
            return []
        invoices = (
            await self.db.execute(
                select(FeeInvoice)
                .where(
                    FeeInvoice.school_id == self.school_id,
                    FeeInvoice.student_fee_account_id.in_(accounts),
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
                .order_by(FeeInvoice.due_date)
            )
        ).scalars().all()
        out = []
        for inv in invoices:
            item = (
                await self.db.execute(
                    select(FeeInvoiceItem).where(
                        FeeInvoiceItem.invoice_id == inv.id, FeeInvoiceItem.school_id == self.school_id
                    )
                )
            ).scalars().first()
            out.append(
                DueLine(
                    invoice_id=inv.id,
                    invoice_number=inv.invoice_number,
                    due_date=inv.due_date,
                    total_amount=money(inv.total_amount),
                    balance_amount=money(inv.balance_amount),
                    status=inv.status,
                    description=item.description if item else None,
                )
            )
        return out

    # ── concessions ───────────────────────────────────────────

    def _rule_out(self, row: FeeConcessionRule) -> ConcessionRuleOut:
        return ConcessionRuleOut(
            id=row.id,
            school_id=row.school_id,
            name=row.name,
            code=row.code,
            rule_type=row.rule_type,
            value_type=row.value_type,
            value=money(row.value),
            fee_category_id=row.fee_category_id,
            student_category=row.student_category,
            active=row.active,
        )

    async def list_rules(self) -> list[ConcessionRuleOut]:
        rows = (
            await self.db.execute(
                select(FeeConcessionRule).where(FeeConcessionRule.school_id == self.school_id)
            )
        ).scalars().all()
        return [self._rule_out(r) for r in rows]

    async def create_rule(self, data: ConcessionRuleIn) -> ConcessionRuleOut:
        if data.fee_category_id:
            assert_same_school(
                await self.db.get(FeeCategory, data.fee_category_id), self.school_id, "Fee head"
            )
        row = FeeConcessionRule(
            id=uuid4(),
            school_id=self.school_id,
            name=data.name.strip(),
            code=data.code.strip().upper(),
            rule_type=data.rule_type,
            value_type=data.value_type,
            value=money(data.value),
            fee_category_id=data.fee_category_id,
            student_category=data.student_category,
            active=data.active,
            created_by=self.user_id,
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError("Concession code already exists")
        await self.db.refresh(row)
        return self._rule_out(row)

    async def patch_rule(self, rule_id: UUID, data: ConcessionRulePatch) -> ConcessionRuleOut:
        row = assert_same_school(
            await self.db.get(FeeConcessionRule, rule_id), self.school_id, "Concession rule"
        )
        if data.name is not None:
            row.name = data.name.strip()
        if data.value is not None:
            row.value = money(data.value)
        if data.active is not None:
            row.active = data.active
        if data.fee_category_id is not None:
            assert_same_school(
                await self.db.get(FeeCategory, data.fee_category_id), self.school_id, "Fee head"
            )
            row.fee_category_id = data.fee_category_id
        if data.student_category is not None:
            row.student_category = data.student_category
        await self.db.commit()
        return self._rule_out(row)

    def _concession_out(self, row: FeeConcessionRequest) -> ConcessionOut:
        return ConcessionOut(
            id=row.id,
            school_id=row.school_id,
            student_id=row.student_id,
            rule_id=row.rule_id,
            fee_category_id=row.fee_category_id,
            value_type=row.value_type,
            value=money(row.value),
            computed_amount=money(row.computed_amount),
            reason=row.reason,
            status=row.status,
            requested_by=row.requested_by,
            approved_by=row.approved_by,
        )

    async def list_concessions(self, status: str | None = None) -> list[ConcessionOut]:
        q = select(FeeConcessionRequest).where(FeeConcessionRequest.school_id == self.school_id)
        if status:
            q = q.where(FeeConcessionRequest.status == status)
        rows = (await self.db.execute(q.order_by(FeeConcessionRequest.created_at.desc()))).scalars().all()
        return [self._concession_out(r) for r in rows]

    async def request_concession(self, data: ConcessionRequestIn) -> ConcessionOut:
        await self._student(data.student_id)
        value_type = data.value_type
        value = money(data.value)
        category_id = data.fee_category_id
        if data.rule_id:
            rule = assert_same_school(
                await self.db.get(FeeConcessionRule, data.rule_id), self.school_id, "Concession rule"
            )
            if not rule.active:
                raise AppError("Concession rule is inactive", status_code=422)
            value_type = rule.value_type
            value = money(rule.value)
            category_id = rule.fee_category_id or category_id
            profile = await self._profile_row(data.student_id)
            if rule.student_category and (not profile or profile.category != rule.student_category):
                raise AppError("Student category does not match the concession rule", status_code=422)
        base = await self._category_balance(data.student_id, category_id)
        computed = (
            money(base * value / Decimal("100"))
            if value_type == ConcessionValueType.PERCENT.value
            else money(min(value, base))
        )
        if computed <= 0:
            raise AppError("Nothing outstanding to concede", status_code=422)
        row = FeeConcessionRequest(
            id=uuid4(),
            school_id=self.school_id,
            student_id=data.student_id,
            rule_id=data.rule_id,
            fee_category_id=category_id,
            value_type=value_type,
            value=value,
            computed_amount=computed,
            reason=data.reason.strip(),
            status=ApprovalStatus.PENDING.value,
            requested_by=self.user_id,
        )
        self.db.add(row)
        self.db.add(
            FinanceApproval(
                id=uuid4(),
                school_id=self.school_id,
                entity_type=FinanceApprovalType.CONCESSION.value,
                entity_id=row.id,
                status=ApprovalStatus.PENDING.value,
                amount=computed,
                reason=row.reason,
                requested_by=self.user_id,
            )
        )
        await self._audit("CONCESSION_REQUESTED", "fee_concession", row.id, new_values={"amount": str(computed)})
        await self.db.commit()
        await self.db.refresh(row)
        return self._concession_out(row)

    async def _category_balance(self, student_id: UUID, category_id: UUID | None) -> Decimal:
        dues = await self._open_invoices(student_id)
        total = money(0)
        for inv in dues:
            if category_id is None:
                total = money(total + inv.balance_amount)
                continue
            items = (
                await self.db.execute(
                    select(FeeInvoiceItem).where(
                        FeeInvoiceItem.school_id == self.school_id,
                        FeeInvoiceItem.invoice_id == inv.id,
                        FeeInvoiceItem.fee_category_id == category_id,
                    )
                )
            ).scalars().all()
            if items:
                total = money(total + min(sum(money(i.total_amount) for i in items), money(inv.balance_amount)))
        return total

    async def _open_invoices(self, student_id: UUID) -> list[FeeInvoice]:
        accounts = (
            await self.db.execute(
                select(StudentFeeAccount.id).where(
                    StudentFeeAccount.school_id == self.school_id,
                    StudentFeeAccount.student_id == student_id,
                )
            )
        ).scalars().all()
        if not accounts:
            return []
        return (
            await self.db.execute(
                select(FeeInvoice)
                .where(
                    FeeInvoice.school_id == self.school_id,
                    FeeInvoice.student_fee_account_id.in_(accounts),
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
                .order_by(FeeInvoice.due_date)
            )
        ).scalars().all()

    async def decide_concession(self, concession_id: UUID, approve: bool, note: str | None) -> ConcessionOut:
        row = (
            await self.db.execute(
                select(FeeConcessionRequest)
                .where(FeeConcessionRequest.id == concession_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        row = assert_same_school(row, self.school_id, "Concession")
        if row.status != ApprovalStatus.PENDING.value:
            raise AppError("Concession is not pending", status_code=422)
        assert_distinct_checker(row.requested_by, self.user_id)
        if approve:
            applied = await self._apply_concession_amount(row.student_id, row.fee_category_id, money(row.computed_amount))
            row.computed_amount = applied
            row.status = ApprovalStatus.APPROVED.value
            acc = (
                await self.db.execute(
                    select(StudentFeeAccount).where(
                        StudentFeeAccount.school_id == self.school_id,
                        StudentFeeAccount.student_id == row.student_id,
                    )
                )
            ).scalars().first()
            if acc:
                adj = FeeAdjustment(
                    id=uuid4(),
                    school_id=self.school_id,
                    student_fee_account_id=acc.id,
                    fee_category_id=row.fee_category_id,
                    type=FeeAdjustmentType.SCHOLARSHIP.value,
                    amount=applied,
                    reason=row.reason,
                    created_by=row.requested_by,
                    approved_by=self.user_id,
                )
                self.db.add(adj)
                await self.db.flush()
                await post_ledger_entry(
                    self.db,
                    school_id=self.school_id,
                    student_id=row.student_id,
                    account_id=acc.id,
                    entry_type=LedgerEntryType.CONCESSION.value,
                    debit=money(0),
                    credit=applied,
                    ref_type="fee_concession",
                    ref_id=row.id,
                    narration=f"Concession: {row.reason[:80]}",
                    actor_user_id=self.user_id,
                )
        else:
            row.status = ApprovalStatus.REJECTED.value
        row.approved_by = self.user_id
        row.decided_at = _now()
        row.decision_note = note
        await self._set_approval(FinanceApprovalType.CONCESSION.value, row.id, approve, note)
        await self._audit(
            "CONCESSION_DECIDED",
            "fee_concession",
            row.id,
            new_values={"status": row.status, "amount": str(row.computed_amount)},
        )
        await self.db.commit()
        await self.db.refresh(row)
        return self._concession_out(row)

    async def _apply_concession_amount(
        self, student_id: UUID, category_id: UUID | None, amount: Decimal
    ) -> Decimal:
        remaining = money(amount)
        applied = money(0)
        for inv in await self._open_invoices(student_id):
            if remaining <= 0:
                break
            locked = (
                await self.db.execute(
                    select(FeeInvoice)
                    .where(FeeInvoice.id == inv.id, FeeInvoice.school_id == self.school_id)
                    .with_for_update()
                )
            ).scalar_one()
            room = money(locked.balance_amount)
            if category_id:
                items = (
                    await self.db.execute(
                        select(FeeInvoiceItem).where(
                            FeeInvoiceItem.school_id == self.school_id,
                            FeeInvoiceItem.invoice_id == locked.id,
                            FeeInvoiceItem.fee_category_id == category_id,
                        )
                    )
                ).scalars().all()
                if not items:
                    continue
                room = money(min(room, sum(money(i.total_amount) for i in items)))
            take = money(min(room, remaining))
            if take <= 0:
                continue
            locked.discount_amount = money(locked.discount_amount) + take
            locked.total_amount = money(locked.total_amount) - take
            locked.balance_amount = money(locked.balance_amount) - take
            locked.status = invoice_status_after_payment(
                total=money(locked.total_amount),
                paid=money(locked.paid_amount),
                due_date=locked.due_date,
            )
            remaining = money(remaining - take)
            applied = money(applied + take)
        return applied

    async def _set_approval(self, entity_type: str, entity_id: UUID, approve: bool, note: str | None) -> None:
        approval = (
            await self.db.execute(
                select(FinanceApproval).where(
                    FinanceApproval.school_id == self.school_id,
                    FinanceApproval.entity_type == entity_type,
                    FinanceApproval.entity_id == entity_id,
                )
            )
        ).scalar_one_or_none()
        if not approval:
            return
        approval.status = ApprovalStatus.APPROVED.value if approve else ApprovalStatus.REJECTED.value
        approval.approved_by = self.user_id
        approval.decided_at = _now()
        approval.decision_note = note

    # ── collection ────────────────────────────────────────────

    async def collect(self, data: CollectIn) -> CollectionOut:
        await self._student(data.student_id)
        await self._assert_fy_open(date.today())
        branch_id = self._require_branch(data.branch_id)
        existing = (
            await self.db.execute(
                select(Payment).where(
                    Payment.school_id == self.school_id,
                    Payment.idempotency_key == data.idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return await self._collection_out(existing)

        alloc_total = sum((money(a.amount) for a in data.allocations), money(0))
        tender_total = sum((money(t.amount) for t in data.tenders), money(0))
        if alloc_total != tender_total:
            raise AppError("Allocation total must equal tender total", status_code=422)
        if alloc_total <= 0:
            raise AppError("Amount must be positive", status_code=422)

        per_invoice: dict[UUID, Decimal] = {}
        for alloc in data.allocations:
            per_invoice[alloc.invoice_id] = money(per_invoice.get(alloc.invoice_id, 0) + alloc.amount)
        invoice_ids = sorted(per_invoice)
        locked: dict[UUID, FeeInvoice] = {}
        account_id = None
        for iid in invoice_ids:
            inv = (
                await self.db.execute(
                    select(FeeInvoice).where(FeeInvoice.id == iid).with_for_update()
                )
            ).scalar_one_or_none()
            inv = assert_same_school(inv, self.school_id, "Invoice")
            if inv.status == FeeInvoiceStatus.CANCELLED.value:
                raise AppError("Invoice cancelled", status_code=422)
            acc = assert_same_school(
                await self.db.get(StudentFeeAccount, inv.student_fee_account_id),
                self.school_id,
                "Fee account",
            )
            if acc.student_id != data.student_id:
                raise NotFoundError("Invoice not found")
            account_id = acc.id
            locked[iid] = inv

        for iid, amt in per_invoice.items():
            if amt > money(locked[iid].balance_amount):
                raise AppError("Amount exceeds invoice balance", status_code=422)

        for tender in data.tenders:
            if tender.method in CHEQUE_METHODS and not (tender.instrument_no and tender.bank_name):
                raise AppError("Cheque/DD requires instrument number and bank", status_code=422)

        settings = await self.fees.get_or_create_settings(self.school_id)
        number = await self.fees.next_number(self.school_id, "PAYMENT", settings.payment_prefix)
        method = data.tenders[0].method if len({t.method for t in data.tenders}) == 1 else PaymentMethod.SPLIT.value
        payment = Payment(
            id=uuid4(),
            school_id=self.school_id,
            student_fee_account_id=account_id,
            invoice_id=data.allocations[0].invoice_id,
            payment_number=number,
            amount=alloc_total,
            currency=settings.currency,
            method=method,
            status=PaymentStatus.SUCCESS.value,
            paid_at=_now(),
            received_by=self.user_id,
            notes=data.notes,
            idempotency_key=data.idempotency_key,
            branch_id=branch_id,
        )
        self.db.add(payment)
        await self.db.flush()
        _year, fy_label = indian_financial_year()
        rnum = await self.fees.next_number(self.school_id, "RECEIPT", settings.receipt_prefix)
        receipt = FeeReceipt(
            id=uuid4(),
            school_id=self.school_id,
            payment_id=payment.id,
            receipt_number=rnum,
            issued_at=_now(),
            generated_by=self.user_id,
            status=ReceiptStatus.ACTIVE.value,
            fy_label=fy_label,
            branch_id=branch_id,
            reprint_count=0,
        )
        self.db.add(receipt)
        await self.db.flush()
        for iid, amt in per_invoice.items():
            inv = locked[iid]
            self.db.add(
                PaymentAllocation(
                    id=uuid4(),
                    school_id=self.school_id,
                    payment_id=payment.id,
                    invoice_id=inv.id,
                    amount=amt,
                )
            )
            inv.paid_amount = money(inv.paid_amount) + amt
            inv.balance_amount = money(inv.total_amount) - money(inv.paid_amount)
            if inv.balance_amount < 0:
                inv.balance_amount = money(0)
            inv.status = invoice_status_after_payment(
                total=money(inv.total_amount), paid=money(inv.paid_amount), due_date=inv.due_date
            )
        for tender in data.tenders:
            status = (
                InstrumentStatus.RECEIVED.value
                if tender.method in CHEQUE_METHODS
                else InstrumentStatus.SETTLED.value
            )
            self.db.add(
                FeePaymentTender(
                    id=uuid4(),
                    school_id=self.school_id,
                    payment_id=payment.id,
                    receipt_id=receipt.id,
                    branch_id=branch_id,
                    method=tender.method,
                    amount=money(tender.amount),
                    instrument_no=tender.instrument_no,
                    bank_name=tender.bank_name,
                    instrument_date=tender.instrument_date,
                    reference=tender.reference,
                    status=status,
                )
            )
        await post_ledger_entry(
            self.db,
            school_id=self.school_id,
            student_id=data.student_id,
            account_id=account_id,
            entry_type=LedgerEntryType.PAYMENT.value,
            debit=money(0),
            credit=alloc_total,
            ref_type="payment",
            ref_id=payment.id,
            narration=f"Counter receipt {rnum}",
            actor_user_id=self.user_id,
            branch_id=branch_id,
        )
        await self._audit(
            "COUNTER_COLLECTION",
            "payment",
            payment.id,
            new_values={"amount": str(alloc_total), "receipt": rnum},
        )
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            await bind_tenant(self.db, self.school_id)
            winner = (
                await self.db.execute(
                    select(Payment).where(
                        Payment.school_id == self.school_id,
                        Payment.idempotency_key == data.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if winner:
                return await self._collection_out(winner)
            raise
        return await self._collection_out(payment)

    async def _collection_out(self, payment: Payment) -> CollectionOut:
        assert_same_school(payment, self.school_id, "Payment")
        receipt = (
            await self.db.execute(
                select(FeeReceipt).where(
                    FeeReceipt.payment_id == payment.id, FeeReceipt.school_id == self.school_id
                )
            )
        ).scalar_one_or_none()
        if not receipt:
            raise NotFoundError("Receipt not found")
        tenders = (
            await self.db.execute(
                select(FeePaymentTender).where(
                    FeePaymentTender.payment_id == payment.id,
                    FeePaymentTender.school_id == self.school_id,
                )
            )
        ).scalars().all()
        allocs = (
            await self.db.execute(
                select(PaymentAllocation).where(
                    PaymentAllocation.payment_id == payment.id,
                    PaymentAllocation.school_id == self.school_id,
                )
            )
        ).scalars().all()
        acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
        return CollectionOut(
            payment_id=payment.id,
            receipt_id=receipt.id,
            receipt_number=receipt.receipt_number,
            receipt_status=receipt.status or ReceiptStatus.ACTIVE.value,
            amount=money(payment.amount),
            method=payment.method,
            student_id=acc.student_id if acc else payment.student_fee_account_id,
            branch_id=payment.branch_id,
            tenders=[
                TenderOut(
                    id=t.id,
                    method=t.method,
                    amount=money(t.amount),
                    status=t.status,
                    instrument_no=t.instrument_no,
                    bank_name=t.bank_name,
                    instrument_date=t.instrument_date,
                    reference=t.reference,
                )
                for t in tenders
            ],
            allocations=[AllocationOut(invoice_id=a.invoice_id, amount=money(a.amount)) for a in allocs],
        )

    async def mark_reprint(self, receipt_id: UUID) -> FeeReceipt:
        receipt = (
            await self.db.execute(
                select(FeeReceipt).where(FeeReceipt.id == receipt_id).with_for_update()
            )
        ).scalar_one_or_none()
        receipt = assert_same_school(receipt, self.school_id, "Receipt")
        receipt.reprint_count = int(receipt.reprint_count or 0) + 1
        await self.db.commit()
        await self.db.refresh(receipt)
        return receipt

    async def request_receipt_cancel(self, receipt_id: UUID, data: CancelReceiptIn) -> ApprovalOut:
        receipt = assert_same_school(await self.db.get(FeeReceipt, receipt_id), self.school_id, "Receipt")
        if receipt.status == ReceiptStatus.CANCELLED.value:
            raise AppError("Receipt already cancelled", status_code=422)
        row = FinanceApproval(
            id=uuid4(),
            school_id=self.school_id,
            entity_type=FinanceApprovalType.RECEIPT_CANCEL.value,
            entity_id=receipt.id,
            status=ApprovalStatus.PENDING.value,
            amount=money(0),
            reason=data.reason.strip(),
            requested_by=self.user_id,
            branch_id=receipt.branch_id,
            payload=json.dumps({"receipt_id": str(receipt.id)}),
        )
        self.db.add(row)
        await self._audit("RECEIPT_CANCEL_REQUESTED", "fee_receipt", receipt.id, reason=data.reason)
        await self.db.commit()
        await self.db.refresh(row)
        return self._approval_out(row)

    async def _approval_out(self, row: FinanceApproval) -> ApprovalOut:
        return ApprovalOut(
            id=row.id,
            school_id=row.school_id,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            status=row.status,
            amount=money(row.amount),
            reason=row.reason,
            requested_by=row.requested_by,
            approved_by=row.approved_by,
            decision_note=row.decision_note,
        )

    async def list_approvals(self, status: str | None = None) -> list[ApprovalOut]:
        q = select(FinanceApproval).where(FinanceApproval.school_id == self.school_id)
        if status:
            q = q.where(FinanceApproval.status == status)
        q = self._limit(q, FinanceApproval.branch_id, None) if self.branch_ids is not None else q
        rows = (await self.db.execute(q.order_by(FinanceApproval.created_at.desc()).limit(200))).scalars().all()
        return [self._approval_out(r) for r in rows]

    async def decide_approval(self, approval_id: UUID, approve: bool, note: str | None) -> ApprovalOut:
        row = (
            await self.db.execute(
                select(FinanceApproval).where(FinanceApproval.id == approval_id).with_for_update()
            )
        ).scalar_one_or_none()
        row = assert_same_school(row, self.school_id, "Approval")
        if row.status != ApprovalStatus.PENDING.value:
            raise AppError("Approval is not pending", status_code=422)
        assert_distinct_checker(row.requested_by, self.user_id)
        if row.entity_type == FinanceApprovalType.RECEIPT_CANCEL.value and approve:
            await self._execute_receipt_cancel(row.entity_id, row.reason)
        elif row.entity_type == FinanceApprovalType.FINE_WAIVER.value and approve:
            await self._execute_waiver(row.entity_id)
        elif row.entity_type == FinanceApprovalType.CONCESSION.value:
            return await self._delegate_concession(row, approve, note)
        elif row.entity_type == FinanceApprovalType.REFUND.value and approve:
            await self.payments.approve_refund(self.school_id, self.user_id, row.entity_id, note=note)
            row = await self.db.get(FinanceApproval, approval_id)
            return self._approval_out(row)
        elif row.entity_type == FinanceApprovalType.REFUND.value and not approve:
            await self.payments.reject_refund(self.school_id, self.user_id, row.entity_id, note=note or "Rejected")
            row = await self.db.get(FinanceApproval, approval_id)
            return self._approval_out(row)
        row.status = ApprovalStatus.APPROVED.value if approve else ApprovalStatus.REJECTED.value
        row.approved_by = self.user_id
        row.decided_at = _now()
        row.decision_note = note
        await self.db.commit()
        await self.db.refresh(row)
        return self._approval_out(row)

    async def _delegate_concession(self, row: FinanceApproval, approve: bool, note: str | None) -> ApprovalOut:
        await self.decide_concession(row.entity_id, approve, note)
        fresh = await self.db.get(FinanceApproval, row.id)
        return self._approval_out(fresh)

    async def _execute_receipt_cancel(self, receipt_id: UUID, reason: str) -> None:
        receipt = (
            await self.db.execute(
                select(FeeReceipt).where(FeeReceipt.id == receipt_id).with_for_update()
            )
        ).scalar_one()
        assert_same_school(receipt, self.school_id, "Receipt")
        if receipt.status == ReceiptStatus.CANCELLED.value:
            return
        payment = (
            await self.db.execute(
                select(Payment).where(Payment.id == receipt.payment_id).with_for_update()
            )
        ).scalar_one()
        assert_same_school(payment, self.school_id, "Payment")
        applied = await reverse_payment_amount(self.db, payment, money(payment.amount))
        payment.status = PaymentStatus.CANCELLED.value
        receipt.status = ReceiptStatus.CANCELLED.value
        receipt.cancel_reason = reason
        receipt.cancelled_at = _now()
        receipt.cancelled_by = self.user_id
        acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
        if acc and acc.school_id == self.school_id:
            await post_ledger_entry(
                self.db,
                school_id=self.school_id,
                student_id=acc.student_id,
                account_id=acc.id,
                entry_type=LedgerEntryType.REVERSAL.value,
                debit=applied,
                credit=money(0),
                ref_type="fee_receipt",
                ref_id=receipt.id,
                narration=f"Cancelled receipt {receipt.receipt_number}: {reason[:80]}",
                actor_user_id=self.user_id,
                branch_id=receipt.branch_id,
            )
        await self._audit("RECEIPT_CANCELLED", "fee_receipt", receipt.id, reason=reason, old_values={"status": "ACTIVE"})

    async def request_waiver(self, data: FineWaiverIn) -> ApprovalOut:
        inv = assert_same_school(await self.db.get(FeeInvoice, data.invoice_id), self.school_id, "Invoice")
        if money(data.amount) > money(inv.balance_amount):
            raise AppError("Waiver exceeds balance", status_code=422)
        acc = assert_same_school(
            await self.db.get(StudentFeeAccount, inv.student_fee_account_id), self.school_id, "Fee account"
        )
        waiver = FineWaiverRequest(
            id=uuid4(),
            school_id=self.school_id,
            invoice_id=inv.id,
            student_id=acc.student_id,
            amount=money(data.amount),
            reason=data.reason.strip(),
            status=ApprovalStatus.PENDING.value,
            requested_by=self.user_id,
        )
        self.db.add(waiver)
        approval = FinanceApproval(
            id=uuid4(),
            school_id=self.school_id,
            entity_type=FinanceApprovalType.FINE_WAIVER.value,
            entity_id=waiver.id,
            status=ApprovalStatus.PENDING.value,
            amount=money(data.amount),
            reason=data.reason.strip(),
            requested_by=self.user_id,
        )
        self.db.add(approval)
        await self.db.commit()
        await self.db.refresh(approval)
        return self._approval_out(approval)

    async def _execute_waiver(self, waiver_id: UUID) -> None:
        waiver = (
            await self.db.execute(
                select(FineWaiverRequest).where(FineWaiverRequest.id == waiver_id).with_for_update()
            )
        ).scalar_one()
        assert_same_school(waiver, self.school_id, "Waiver")
        assert_distinct_checker(waiver.requested_by, self.user_id)
        applied = await self._apply_concession_amount(waiver.student_id, None, money(waiver.amount))
        waiver.status = ApprovalStatus.APPROVED.value
        waiver.approved_by = self.user_id
        waiver.decided_at = _now()
        acc = (
            await self.db.execute(
                select(StudentFeeAccount).where(
                    StudentFeeAccount.school_id == self.school_id,
                    StudentFeeAccount.student_id == waiver.student_id,
                )
            )
        ).scalars().first()
        await post_ledger_entry(
            self.db,
            school_id=self.school_id,
            student_id=waiver.student_id,
            account_id=acc.id if acc else None,
            entry_type=LedgerEntryType.WAIVER.value,
            debit=money(0),
            credit=applied,
            ref_type="fee_fine_waiver",
            ref_id=waiver.id,
            narration=f"Fine waiver: {waiver.reason[:80]}",
            actor_user_id=self.user_id,
        )

    # ── cheques ───────────────────────────────────────────────

    async def list_cheques(self, status: str | None = None) -> list[ChequeOut]:
        q = select(FeePaymentTender).where(
            FeePaymentTender.school_id == self.school_id,
            FeePaymentTender.method.in_(list(CHEQUE_METHODS)),
        )
        if status:
            q = q.where(FeePaymentTender.status == status)
        q = self._limit(q, FeePaymentTender.branch_id, None)
        rows = (await self.db.execute(q.order_by(FeePaymentTender.created_at.desc()).limit(300))).scalars().all()
        out = []
        for t in rows:
            payment = await self.db.get(Payment, t.payment_id)
            receipt = await self.db.get(FeeReceipt, t.receipt_id) if t.receipt_id else None
            acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id) if payment else None
            out.append(
                ChequeOut(
                    id=t.id,
                    school_id=t.school_id,
                    payment_id=t.payment_id,
                    receipt_id=t.receipt_id,
                    receipt_number=receipt.receipt_number if receipt else None,
                    branch_id=t.branch_id,
                    method=t.method,
                    amount=money(t.amount),
                    status=t.status,
                    instrument_no=t.instrument_no,
                    bank_name=t.bank_name,
                    instrument_date=t.instrument_date,
                    student_id=acc.student_id if acc else None,
                )
            )
        return out

    async def _tender(self, tender_id: UUID) -> FeePaymentTender:
        row = (
            await self.db.execute(
                select(FeePaymentTender).where(FeePaymentTender.id == tender_id).with_for_update()
            )
        ).scalar_one_or_none()
        row = assert_same_school(row, self.school_id, "Cheque")
        if row.method not in CHEQUE_METHODS:
            raise AppError("Not a cheque or DD", status_code=422)
        if self.branch_ids is not None and row.branch_id not in self.branch_ids:
            raise ForbiddenError("Branch is outside your assignment")
        return row

    async def deposit_cheque(self, tender_id: UUID) -> ChequeOut:
        row = await self._tender(tender_id)
        if row.status != InstrumentStatus.RECEIVED.value:
            raise AppError("Only received instruments can be deposited", status_code=422)
        row.status = InstrumentStatus.DEPOSITED.value
        row.deposited_at = _now()
        await self._audit("CHEQUE_DEPOSITED", "fee_payment_tender", row.id)
        await self.db.commit()
        return await self._cheque_one(row.id)

    async def clear_cheque(self, tender_id: UUID) -> ChequeOut:
        row = await self._tender(tender_id)
        if row.status != InstrumentStatus.DEPOSITED.value:
            raise AppError("Only deposited instruments can be cleared", status_code=422)
        row.status = InstrumentStatus.CLEARED.value
        row.cleared_at = _now()
        await self._audit("CHEQUE_CLEARED", "fee_payment_tender", row.id)
        await self.db.commit()
        return await self._cheque_one(row.id)

    async def bounce_cheque(self, tender_id: UUID, data: BounceIn) -> ChequeOut:
        row = await self._tender(tender_id)
        if row.status not in (
            InstrumentStatus.RECEIVED.value,
            InstrumentStatus.DEPOSITED.value,
            InstrumentStatus.CLEARED.value,
        ):
            raise AppError("Instrument cannot be bounced", status_code=422)
        payment = (
            await self.db.execute(select(Payment).where(Payment.id == row.payment_id).with_for_update())
        ).scalar_one()
        assert_same_school(payment, self.school_id, "Payment")
        applied = await reverse_payment_amount(self.db, payment, money(row.amount))
        row.status = InstrumentStatus.BOUNCED.value
        row.bounced_at = _now()
        row.bounce_reason = data.reason.strip()
        row.bounce_charge = money(data.charge)
        receipt = await self.db.get(FeeReceipt, row.receipt_id) if row.receipt_id else None
        if receipt and receipt.school_id == self.school_id:
            if applied >= money(payment.amount):
                receipt.status = ReceiptStatus.CANCELLED.value
                receipt.cancel_reason = f"Cheque bounced: {data.reason}"
                receipt.cancelled_at = _now()
                receipt.cancelled_by = self.user_id
                payment.status = PaymentStatus.CANCELLED.value
            else:
                receipt.status = ReceiptStatus.PARTIALLY_REVERSED.value
        acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
        if acc and acc.school_id == self.school_id:
            await post_ledger_entry(
                self.db,
                school_id=self.school_id,
                student_id=acc.student_id,
                account_id=acc.id,
                entry_type=LedgerEntryType.BOUNCE.value,
                debit=applied,
                credit=money(0),
                ref_type="fee_payment_tender",
                ref_id=row.id,
                narration=f"Cheque bounce {row.instrument_no or ''}: {data.reason[:80]}",
                actor_user_id=self.user_id,
                branch_id=row.branch_id,
            )
            if money(data.charge) > 0:
                inv = await self.db.get(FeeInvoice, payment.invoice_id) if payment.invoice_id else None
                if inv and inv.school_id == self.school_id:
                    inv.total_amount = money(inv.total_amount) + money(data.charge)
                    inv.balance_amount = money(inv.balance_amount) + money(data.charge)
                    inv.status = invoice_status_after_payment(
                        total=money(inv.total_amount),
                        paid=money(inv.paid_amount),
                        due_date=inv.due_date,
                    )
                await post_ledger_entry(
                    self.db,
                    school_id=self.school_id,
                    student_id=acc.student_id,
                    account_id=acc.id,
                    entry_type=LedgerEntryType.FINE.value,
                    debit=money(data.charge),
                    credit=money(0),
                    ref_type="cheque_bounce_charge",
                    ref_id=row.id,
                    narration="Cheque bounce charge",
                    actor_user_id=self.user_id,
                )
        await self._audit("CHEQUE_BOUNCED", "fee_payment_tender", row.id, reason=data.reason)
        await self.db.commit()
        return await self._cheque_one(row.id)

    async def _cheque_one(self, tender_id: UUID) -> ChequeOut:
        rows = await self.list_cheques()
        for row in rows:
            if row.id == tender_id:
                return row
        raise NotFoundError("Cheque not found")

    # ── ledger / day close / dashboard / reports ──────────────

    async def ledger(self, student_id: UUID) -> list[LedgerRow]:
        await self._student(student_id)
        await ensure_ledger_backfill(self.db, self.school_id, student_id)
        await self.db.commit()
        rows = (
            await self.db.execute(
                select(FeeLedgerEntry)
                .where(
                    FeeLedgerEntry.school_id == self.school_id,
                    FeeLedgerEntry.student_id == student_id,
                )
                .order_by(FeeLedgerEntry.entry_date, FeeLedgerEntry.created_at)
            )
        ).scalars().all()
        balance = money(0)
        out = []
        for row in rows:
            balance = money(balance + money(row.debit) - money(row.credit))
            out.append(
                LedgerRow(
                    id=row.id,
                    entry_date=row.entry_date,
                    entry_type=row.entry_type,
                    debit=money(row.debit),
                    credit=money(row.credit),
                    balance=balance,
                    narration=row.narration,
                    ref_type=row.ref_type,
                    ref_id=row.ref_id,
                )
            )
        return out

    async def _mode_totals(self, cashier_id: UUID, business_date: date, branch_id: UUID | None) -> dict[str, Decimal]:
        buckets = {k: money(0) for k in ("cash", "cheque", "upi", "card", "neft", "online")}
        day_expr = func.date(func.timezone("Asia/Kolkata", Payment.paid_at))
        q = select(Payment).where(
            Payment.school_id == self.school_id,
            Payment.received_by == cashier_id,
            Payment.status == PaymentStatus.SUCCESS.value,
            day_expr == business_date,
        )
        q = self._limit(q, Payment.branch_id, branch_id)
        payments = (await self.db.execute(q)).scalars().all()
        for payment in payments:
            tenders = (
                await self.db.execute(
                    select(FeePaymentTender).where(
                        FeePaymentTender.school_id == self.school_id,
                        FeePaymentTender.payment_id == payment.id,
                        FeePaymentTender.status != InstrumentStatus.BOUNCED.value,
                    )
                )
            ).scalars().all()
            if tenders:
                for tender in tenders:
                    key = MODE_BUCKETS.get(tender.method, "online")
                    buckets[key] = money(buckets[key] + tender.amount)
            else:
                key = MODE_BUCKETS.get(payment.method, "online")
                buckets[key] = money(buckets[key] + payment.amount)
        return buckets

    def _day_out(self, row: FeeDayClose) -> DayCloseOut:
        return DayCloseOut(
            id=row.id,
            school_id=row.school_id,
            branch_id=row.branch_id,
            cashier_user_id=row.cashier_user_id,
            business_date=row.business_date,
            opening_cash=money(row.opening_cash),
            physical_cash=money(row.physical_cash),
            system_cash=money(row.system_cash),
            system_cheque=money(row.system_cheque),
            system_upi=money(row.system_upi),
            system_card=money(row.system_card),
            system_neft=money(row.system_neft),
            system_online=money(row.system_online),
            system_total=money(row.system_total),
            variance=money(row.variance),
            notes=row.notes,
            status=row.status,
            verified_by=row.verified_by,
        )

    async def submit_day_close(self, data: DayCloseIn) -> DayCloseOut:
        branch_id = self._require_branch(data.branch_id)
        totals = await self._mode_totals(self.user_id, data.business_date, branch_id)
        grand = money(sum(totals.values(), money(0)))
        row = FeeDayClose(
            id=uuid4(),
            school_id=self.school_id,
            branch_id=branch_id,
            cashier_user_id=self.user_id,
            business_date=data.business_date,
            opening_cash=money(data.opening_cash),
            physical_cash=money(data.physical_cash),
            system_cash=totals["cash"],
            system_cheque=totals["cheque"],
            system_upi=totals["upi"],
            system_card=totals["card"],
            system_neft=totals["neft"],
            system_online=totals["online"],
            system_total=grand,
            variance=money(money(data.physical_cash) - totals["cash"]),
            notes=data.notes,
            status=DayCloseStatus.SUBMITTED.value,
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError("Day close already submitted for this cashier and date")
        await self.db.refresh(row)
        return self._day_out(row)

    async def list_day_close(self, business_date: date | None = None) -> list[DayCloseOut]:
        q = select(FeeDayClose).where(FeeDayClose.school_id == self.school_id)
        if business_date:
            q = q.where(FeeDayClose.business_date == business_date)
        if self.role == UserRole.CASHIER.value:
            q = q.where(FeeDayClose.cashier_user_id == self.user_id)
        q = self._limit(q, FeeDayClose.branch_id, None)
        rows = (await self.db.execute(q.order_by(FeeDayClose.business_date.desc()))).scalars().all()
        return [self._day_out(r) for r in rows]

    async def verify_day_close(self, day_id: UUID, approve: bool, note: str | None) -> DayCloseOut:
        row = (
            await self.db.execute(select(FeeDayClose).where(FeeDayClose.id == day_id).with_for_update())
        ).scalar_one_or_none()
        row = assert_same_school(row, self.school_id, "Day close")
        if row.status != DayCloseStatus.SUBMITTED.value:
            raise AppError("Day close is not awaiting verification", status_code=422)
        assert_distinct_checker(row.cashier_user_id, self.user_id)
        row.status = DayCloseStatus.VERIFIED.value if approve else DayCloseStatus.REJECTED.value
        row.verified_by = self.user_id
        row.verified_at = _now()
        if note:
            row.notes = (row.notes or "") + f"\n{note}"
        await self._audit("DAY_CLOSE_DECIDED", "fee_day_close", row.id, new_values={"status": row.status})
        await self.db.commit()
        return self._day_out(row)

    async def dashboard(self) -> DashboardOut:
        settings = await self.fees.get_or_create_settings(self.school_id)
        today = date.today()
        start = today - timedelta(days=13)
        day_expr = func.date(func.timezone("Asia/Kolkata", Payment.paid_at))
        q = select(Payment).where(
            Payment.school_id == self.school_id,
            Payment.status == PaymentStatus.SUCCESS.value,
            day_expr >= start,
        )
        q = self._limit(q, Payment.branch_id, None)
        payments = (await self.db.execute(q)).scalars().all()
        today_by_mode: dict[str, Decimal] = {}
        today_total = money(0)
        trend_map: dict[date, Decimal] = {start + timedelta(days=i): money(0) for i in range(14)}
        for payment in payments:
            paid_day = payment.paid_at.date() if payment.paid_at else payment.created_at.date()
            if paid_day in trend_map:
                trend_map[paid_day] = money(trend_map[paid_day] + payment.amount)
            if paid_day == today:
                today_total = money(today_total + payment.amount)
                today_by_mode[payment.method] = money(today_by_mode.get(payment.method, 0) + payment.amount)
        outstanding = (
            await self.db.execute(
                select(func.coalesce(func.sum(FeeInvoice.balance_amount), 0)).where(
                    FeeInvoice.school_id == self.school_id,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
            )
        ).scalar_one()
        overdue = (
            await self.db.execute(
                select(func.coalesce(func.sum(FeeInvoice.balance_amount), 0)).where(
                    FeeInvoice.school_id == self.school_id,
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.due_date < today,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
            )
        ).scalar_one()
        pending = (
            await self.db.execute(
                select(func.count())
                .select_from(FinanceApproval)
                .where(
                    FinanceApproval.school_id == self.school_id,
                    FinanceApproval.status == ApprovalStatus.PENDING.value,
                )
            )
        ).scalar_one()
        return DashboardOut(
            currency=settings.currency,
            today_collection=today_total,
            today_by_mode={k: money(v) for k, v in today_by_mode.items()},
            outstanding=money(outstanding),
            overdue=money(overdue),
            pending_approvals=int(pending or 0),
            trend=[{"date": d.isoformat(), "amount": str(trend_map[d])} for d in sorted(trend_map)],
        )

    async def report_defaulters(self) -> list[dict]:
        today = date.today()
        rows = (
            await self.db.execute(
                select(FeeInvoice).where(
                    FeeInvoice.school_id == self.school_id,
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.due_date < today,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
            )
        ).scalars().all()
        out = []
        for inv in rows:
            acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
            if not acc or acc.school_id != self.school_id:
                continue
            name = await self.fees._student_name(acc.student_id)
            out.append(
                {
                    "student": name,
                    "student_id": str(acc.student_id),
                    "invoice": inv.invoice_number,
                    "due_date": str(inv.due_date),
                    "balance": str(money(inv.balance_amount)),
                    "days_overdue": (today - inv.due_date).days,
                    "status": inv.status,
                }
            )
        return out

    async def report_day_book(self, on: date, branch_id: UUID | None = None) -> list[dict]:
        day_expr = func.date(func.timezone("Asia/Kolkata", FeeReceipt.issued_at))
        q = select(FeeReceipt).where(FeeReceipt.school_id == self.school_id, day_expr == on)
        q = self._limit(q, FeeReceipt.branch_id, branch_id)
        receipts = (await self.db.execute(q.order_by(FeeReceipt.issued_at))).scalars().all()
        out = []
        for receipt in receipts:
            payment = await self.db.get(Payment, receipt.payment_id)
            if not payment or payment.school_id != self.school_id:
                continue
            acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
            name = await self.fees._student_name(acc.student_id) if acc else ""
            collector = await self.db.get(User, payment.received_by) if payment.received_by else None
            out.append(
                {
                    "receipt": receipt.receipt_number,
                    "status": receipt.status,
                    "student": name,
                    "amount": str(money(payment.amount)),
                    "method": payment.method,
                    "collector": collector.full_name if collector else "",
                    "time": receipt.issued_at.isoformat(),
                }
            )
        return out

    async def report_head_wise(self, from_date: date | None, to_date: date | None) -> list[dict]:
        q = select(PaymentAllocation, Payment).join(Payment, Payment.id == PaymentAllocation.payment_id).where(
            PaymentAllocation.school_id == self.school_id,
            Payment.school_id == self.school_id,
            Payment.status == PaymentStatus.SUCCESS.value,
        )
        q = self._limit(q, Payment.branch_id, None)
        rows = (await self.db.execute(q)).all()
        buckets: dict[str, Decimal] = {}
        for alloc, payment in rows:
            paid_day = (payment.paid_at or payment.created_at).date()
            if from_date and paid_day < from_date:
                continue
            if to_date and paid_day > to_date:
                continue
            items = (
                await self.db.execute(
                    select(FeeInvoiceItem).where(
                        FeeInvoiceItem.school_id == self.school_id,
                        FeeInvoiceItem.invoice_id == alloc.invoice_id,
                    )
                )
            ).scalars().all()
            base = sum((money(i.total_amount) for i in items), money(0))
            if not items or base <= 0:
                buckets["Unallocated"] = money(buckets.get("Unallocated", 0) + alloc.amount)
                continue
            for item in items:
                share = money(money(alloc.amount) * money(item.total_amount) / base)
                cat = await self.db.get(FeeCategory, item.fee_category_id) if item.fee_category_id else None
                label = cat.name if cat and cat.school_id == self.school_id else (item.description or "Fee")
                buckets[label] = money(buckets.get(label, 0) + share)
        return [{"head": k, "amount": str(v)} for k, v in sorted(buckets.items())]

    async def report_mode_wise(self, from_date: date | None, to_date: date | None) -> list[dict]:
        q = select(Payment).where(
            Payment.school_id == self.school_id, Payment.status == PaymentStatus.SUCCESS.value
        )
        q = self._limit(q, Payment.branch_id, None)
        payments = (await self.db.execute(q)).scalars().all()
        buckets: dict[str, Decimal] = {}
        for payment in payments:
            paid_day = (payment.paid_at or payment.created_at).date()
            if from_date and paid_day < from_date:
                continue
            if to_date and paid_day > to_date:
                continue
            tenders = (
                await self.db.execute(
                    select(FeePaymentTender).where(
                        FeePaymentTender.school_id == self.school_id,
                        FeePaymentTender.payment_id == payment.id,
                        FeePaymentTender.status != InstrumentStatus.BOUNCED.value,
                    )
                )
            ).scalars().all()
            lines = [(t.method, t.amount) for t in tenders] or [(payment.method, payment.amount)]
            for method, amount in lines:
                buckets[method] = money(buckets.get(method, 0) + amount)
        return [{"method": k, "amount": str(v)} for k, v in sorted(buckets.items())]

    async def report_collector_wise(self, from_date: date | None, to_date: date | None) -> list[dict]:
        q = select(Payment).where(
            Payment.school_id == self.school_id, Payment.status == PaymentStatus.SUCCESS.value
        )
        q = self._limit(q, Payment.branch_id, None)
        payments = (await self.db.execute(q)).scalars().all()
        buckets: dict[UUID | None, Decimal] = {}
        for payment in payments:
            paid_day = (payment.paid_at or payment.created_at).date()
            if from_date and paid_day < from_date:
                continue
            if to_date and paid_day > to_date:
                continue
            buckets[payment.received_by] = money(buckets.get(payment.received_by, 0) + payment.amount)
        out = []
        for user_id, amount in buckets.items():
            user = await self.db.get(User, user_id) if user_id else None
            out.append({"collector": user.full_name if user else "Online / unknown", "amount": str(amount)})
        return out

    async def report_branch_wise(self, from_date: date | None, to_date: date | None) -> list[dict]:
        q = select(Payment).where(
            Payment.school_id == self.school_id, Payment.status == PaymentStatus.SUCCESS.value
        )
        q = self._limit(q, Payment.branch_id, None)
        payments = (await self.db.execute(q)).scalars().all()
        buckets: dict[str, Decimal] = {}
        for payment in payments:
            paid_day = (payment.paid_at or payment.created_at).date()
            if from_date and paid_day < from_date:
                continue
            if to_date and paid_day > to_date:
                continue
            key = str(payment.branch_id) if payment.branch_id else ""
            buckets[key] = money(buckets.get(key, 0) + payment.amount)
        out = []
        for key, amount in sorted(buckets.items()):
            label = "Unassigned"
            if key:
                branch = await self.db.get(SchoolBranch, UUID(key))
                if branch and branch.school_id == self.school_id:
                    label = branch.name
            out.append({"branch": label, "branch_id": key or None, "amount": str(amount)})
        return out

    async def report_category_wise(self, from_date: date | None, to_date: date | None) -> list[dict]:
        """Collection grouped by the student fee category (general, staff, RTE), not the fee head."""
        q = select(Payment).where(
            Payment.school_id == self.school_id, Payment.status == PaymentStatus.SUCCESS.value
        )
        q = self._limit(q, Payment.branch_id, None)
        payments = (await self.db.execute(q)).scalars().all()
        buckets: dict[str, Decimal] = {}
        for payment in payments:
            paid_day = (payment.paid_at or payment.created_at).date()
            if from_date and paid_day < from_date:
                continue
            if to_date and paid_day > to_date:
                continue
            category = "GENERAL"
            account = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
            if account and account.school_id == self.school_id:
                profile = (
                    await self.db.execute(
                        select(StudentFeeProfile).where(
                            StudentFeeProfile.school_id == self.school_id,
                            StudentFeeProfile.student_id == account.student_id,
                        )
                    )
                ).scalar_one_or_none()
                if profile:
                    category = profile.category
            buckets[category] = money(buckets.get(category, 0) + payment.amount)
        return [{"category": k, "amount": str(v)} for k, v in sorted(buckets.items())]

    async def list_notifications(self, limit: int = 50):
        from src.db.models.notification import Notification
        from src.modules.finance.schemas import FinanceNotificationOut
        from src.shared.enums import NotificationType

        categories = [item.value for item in NotificationType if item.value.startswith("FEE_")]
        rows = (
            await self.db.execute(
                select(Notification)
                .where(
                    Notification.user_id == self.user_id,
                    or_(Notification.school_id == self.school_id, Notification.school_id.is_(None)),
                    Notification.category.in_(categories),
                )
                .order_by(Notification.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        return [FinanceNotificationOut.model_validate(row) for row in rows]

    async def report_concessions(self, status: str | None = None) -> list[dict]:
        q = select(FeeConcessionRequest).where(FeeConcessionRequest.school_id == self.school_id)
        if status:
            q = q.where(FeeConcessionRequest.status == status)
        rows = (
            await self.db.execute(q.order_by(FeeConcessionRequest.created_at.desc()))
        ).scalars().all()
        out = []
        for row in rows:
            name = await self.fees._student_name(row.student_id)
            out.append(
                {
                    "student": name,
                    "student_id": str(row.student_id),
                    "status": row.status,
                    "value_type": row.value_type,
                    "value": str(money(row.value)),
                    "amount": str(money(row.computed_amount)),
                    "reason": row.reason,
                }
            )
        return out

    async def report_cancelled_receipts(self) -> list[dict]:
        q = select(FeeReceipt).where(
            FeeReceipt.school_id == self.school_id,
            FeeReceipt.status == ReceiptStatus.CANCELLED.value,
        )
        q = self._limit(q, FeeReceipt.branch_id, None)
        receipts = (
            await self.db.execute(q.order_by(nulls_last(FeeReceipt.cancelled_at.desc())))
        ).scalars().all()
        out = []
        for receipt in receipts:
            payment = await self.db.get(Payment, receipt.payment_id)
            if not payment or payment.school_id != self.school_id:
                continue
            acc = await self.db.get(StudentFeeAccount, payment.student_fee_account_id)
            name = await self.fees._student_name(acc.student_id) if acc else ""
            out.append(
                {
                    "receipt": receipt.receipt_number,
                    "student": name,
                    "amount": str(money(payment.amount)),
                    "reason": receipt.cancel_reason or "",
                    "cancelled_at": receipt.cancelled_at.isoformat() if receipt.cancelled_at else "",
                }
            )
        return out

    async def list_receipts(self, status: str | None = None, limit: int = 100) -> list[ReceiptOut]:
        q = select(FeeReceipt).where(FeeReceipt.school_id == self.school_id)
        if status:
            q = q.where(FeeReceipt.status == status)
        q = self._limit(q, FeeReceipt.branch_id, None)
        rows = (
            await self.db.execute(
                q.order_by(FeeReceipt.issued_at.desc()).limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        return [await self.payments.get_receipt(self.school_id, row.id) for row in rows]

    async def list_reminder_log(self, limit: int = 200) -> list[ReminderLogOut]:
        rows = (
            await self.db.execute(
                select(FeeReminderLog)
                .where(FeeReminderLog.school_id == self.school_id)
                .order_by(FeeReminderLog.sent_on.desc(), FeeReminderLog.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
        return [
            ReminderLogOut(
                id=row.id,
                school_id=row.school_id,
                invoice_id=row.invoice_id,
                kind=row.kind,
                sent_on=row.sent_on,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def list_audit(self) -> list[AuditRow]:
        rows = (
            await self.db.execute(
                select(FeeAuditLog)
                .where(FeeAuditLog.school_id == self.school_id)
                .order_by(FeeAuditLog.created_at.desc())
                .limit(200)
            )
        ).scalars().all()
        return [
            AuditRow(
                id=r.id,
                action=r.action,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                actor_user_id=r.actor_user_id,
                reason=r.reason,
                old_values=r.old_values,
                new_values=r.new_values,
                ip_address=r.ip_address,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def schedule_reminders(self) -> dict:
        from src.core.jobs import enqueue_job

        try:
            await enqueue_job("fee_reminders", {"school_id": str(self.school_id)})
        except Exception as exc:
            raise AppError(f"Could not queue fee reminders: {exc}", status_code=503) from exc
        return {"queued": True, "school_id": str(self.school_id), "job": "fee_reminders"}

    async def request_refund(self, payment_id: UUID, data: RefundIn):
        return await self.payments.request_refund(self.school_id, self.user_id, payment_id, data)
