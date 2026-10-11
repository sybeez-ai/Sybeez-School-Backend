"""Fee configuration, structures, accounts, invoices, ledger, dashboard."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import get_settings
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    Enrollment,
    FeeAdjustment,
    FeeAuditLog,
    FeeCategory,
    FeeInstallment,
    FeeInvoice,
    FeeInvoiceItem,
    FeeReceipt,
    FeeSequence,
    FeeSettings,
    FeeReminderLog,
    FeeStructure,
    FeeStructureItem,
    LateFeeRule,
    Parent,
    Payment,
    School,
    Student,
    StudentParent,
    User,
)
from src.db.models.class_ import ClassRoom
from src.modules.fees.calculations import calculate_late_fee, invoice_status_after_payment, money
from src.modules.fees.schemas import (
    AdjustmentCreate,
    AdjustmentOut,
    CollectionRowOut,
    FeeCategoryCreate,
    FeeCategoryOut,
    FeeCategoryUpdate,
    FeeSettingsOut,
    FeeSettingsUpdate,
    FeeStructureCreate,
    FeeStructureOut,
    FeeStructureUpdate,
    FeesDashboardOut,
    GenerateResultOut,
    InstallmentIn,
    InstallmentOut,
    InvoiceGenerateIn,
    InvoiceItemOut,
    InvoiceOut,
    LateFeeRuleIn,
    LateFeeRuleOut,
    LedgerEntryOut,
    OutstandingRowOut,
    PaymentOut,
    StructureItemIn,
    StructureItemOut,
    StudentFeeSummaryOut,
)
from src.modules.notifications.service import create_in_app_notification
from src.shared.enums import (
    EnrollmentStatus,
    FeeInvoiceStatus,
    FeeStructureStatus,
    NotificationType,
    PaymentStatus,
    StudentFeeAccountStatus,
    UserRole,
)
from src.db.models.fee import StudentFeeAccount


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _audit(
        self,
        school_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        *,
        reason: str | None = None,
        old_values: dict | None = None,
        new_values: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        self.db.add(
            FeeAuditLog(
                id=uuid4(),
                school_id=school_id,
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                reason=reason,
                old_values=json.dumps(old_values, default=str) if old_values else None,
                new_values=json.dumps(new_values, default=str) if new_values else None,
                ip_address=ip_address,
            )
        )

    async def next_number(self, school_id: UUID, seq_type: str, prefix: str) -> str:
        from src.modules.fees.numbering import allocate_series_number
        from src.modules.fees.tenant import bind_tenant

        await bind_tenant(self.db, school_id)
        return await allocate_series_number(self.db, school_id, seq_type, prefix)

    async def get_or_create_settings(self, school_id: UUID) -> FeeSettings:
        row = (
            await self.db.execute(select(FeeSettings).where(FeeSettings.school_id == school_id))
        ).scalar_one_or_none()
        if row:
            return row
        row = FeeSettings(id=uuid4(), school_id=school_id)
        # Production default: prefer Razorpay when credentials are configured.
        core = get_settings()
        if (core.app_env or "").lower() == "production":
            from src.modules.fees.razorpay_secrets import razorpay_env

            key_id = razorpay_env("RAZORPAY_KEY_ID")
            key_secret = razorpay_env("RAZORPAY_KEY_SECRET")
            if key_id and key_secret:
                row.gateway_provider = "razorpay"
                row.gateway_public_key = key_id
        self.db.add(row)
        await self.db.flush()
        return row

    # ── categories ────────────────────────────────────────────

    async def list_categories(self, school_id: UUID) -> list[FeeCategoryOut]:
        rows = (
            await self.db.execute(
                select(FeeCategory)
                .where(FeeCategory.school_id == school_id)
                .order_by(FeeCategory.name)
            )
        ).scalars().all()
        return [FeeCategoryOut.model_validate(r) for r in rows]

    async def create_category(
        self, school_id: UUID, user_id: UUID, data: FeeCategoryCreate
    ) -> FeeCategoryOut:
        code = data.code.strip().upper()
        exists = (
            await self.db.execute(
                select(FeeCategory).where(
                    FeeCategory.school_id == school_id, FeeCategory.code == code
                )
            )
        ).scalar_one_or_none()
        if exists:
            raise ConflictError("Category code already exists")
        row = FeeCategory(
            id=uuid4(),
            school_id=school_id,
            name=data.name.strip(),
            code=code,
            description=data.description,
            active=data.active,
            is_80c_eligible=bool(getattr(data, "is_80c_eligible", False)),
            gst_rate=money(getattr(data, "gst_rate", 0) or 0),
            tally_ledger=getattr(data, "tally_ledger", None),
        )
        self.db.add(row)
        await self._audit(school_id, user_id, "FEE_CATEGORY_CREATED", "fee_category", row.id)
        await self.db.commit()
        await self.db.refresh(row)
        return FeeCategoryOut.model_validate(row)

    async def update_category(
        self, school_id: UUID, category_id: UUID, data: FeeCategoryUpdate
    ) -> FeeCategoryOut:
        row = await self.db.get(FeeCategory, category_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Category not found")
        if data.name is not None:
            row.name = data.name.strip()
        if data.description is not None:
            row.description = data.description
        if data.active is not None:
            row.active = data.active
        if data.is_80c_eligible is not None:
            row.is_80c_eligible = data.is_80c_eligible
        if data.gst_rate is not None:
            row.gst_rate = money(data.gst_rate)
        if data.tally_ledger is not None:
            row.tally_ledger = data.tally_ledger
        await self.db.commit()
        await self.db.refresh(row)
        return FeeCategoryOut.model_validate(row)

    # ── structures ────────────────────────────────────────────

    async def _structure_out(self, s: FeeStructure) -> FeeStructureOut:
        items_out: list[StructureItemOut] = []
        total = money(0)
        for it in s.items or []:
            cat = await self.db.get(FeeCategory, it.fee_category_id)
            total += money(it.amount)
            items_out.append(
                StructureItemOut(
                    id=it.id,
                    fee_category_id=it.fee_category_id,
                    amount=money(it.amount),
                    taxable=it.taxable,
                    refundable=it.refundable,
                    mandatory=it.mandatory,
                    description=it.description,
                    category_name=cat.name if cat else None,
                    category_code=cat.code if cat else None,
                )
            )
        inst = [
            InstallmentOut.model_validate(i)
            for i in sorted(s.installments or [], key=lambda x: x.sequence)
        ]
        return FeeStructureOut(
            id=s.id,
            school_id=s.school_id,
            academic_year_id=s.academic_year_id,
            name=s.name,
            grade_id=s.grade_id,
            section_id=s.section_id,
            charge_model=s.charge_model,
            status=s.status,
            active=s.active,
            currency=s.currency,
            effective_from=s.effective_from,
            effective_to=s.effective_to,
            total_amount=total,
            items=items_out,
            installments=inst,
        )

    async def list_structures(
        self, school_id: UUID, academic_year_id: UUID | None = None
    ) -> list[FeeStructureOut]:
        q = (
            select(FeeStructure)
            .where(FeeStructure.school_id == school_id)
            .options(
                selectinload(FeeStructure.items),
                selectinload(FeeStructure.installments),
            )
            .order_by(FeeStructure.name)
        )
        if academic_year_id:
            q = q.where(FeeStructure.academic_year_id == academic_year_id)
        rows = (await self.db.execute(q)).scalars().unique().all()
        return [await self._structure_out(r) for r in rows]

    async def get_structure(self, school_id: UUID, structure_id: UUID) -> FeeStructureOut:
        s = (
            await self.db.execute(
                select(FeeStructure)
                .where(FeeStructure.id == structure_id, FeeStructure.school_id == school_id)
                .options(
                    selectinload(FeeStructure.items),
                    selectinload(FeeStructure.installments),
                )
            )
        ).scalar_one_or_none()
        if not s:
            raise NotFoundError("Fee structure not found")
        return await self._structure_out(s)

    async def create_structure(
        self, school_id: UUID, user_id: UUID, data: FeeStructureCreate
    ) -> FeeStructureOut:
        s = FeeStructure(
            id=uuid4(),
            school_id=school_id,
            academic_year_id=data.academic_year_id,
            name=data.name.strip(),
            grade_id=data.grade_id,
            section_id=data.section_id,
            charge_model=data.charge_model,
            currency=data.currency or "INR",
            effective_from=data.effective_from,
            effective_to=data.effective_to,
            created_by=user_id,
            status=FeeStructureStatus.DRAFT.value,
        )
        self.db.add(s)
        await self.db.flush()
        for it in data.items:
            self.db.add(
                FeeStructureItem(
                    id=uuid4(),
                    school_id=school_id,
                    fee_structure_id=s.id,
                    fee_category_id=it.fee_category_id,
                    amount=money(it.amount),
                    taxable=it.taxable,
                    refundable=it.refundable,
                    mandatory=it.mandatory,
                    description=it.description,
                )
            )
        for inst in data.installments:
            self.db.add(
                FeeInstallment(
                    id=uuid4(),
                    school_id=school_id,
                    fee_structure_id=s.id,
                    name=inst.name.strip(),
                    due_date=inst.due_date,
                    sequence=inst.sequence,
                    amount=money(inst.amount),
                )
            )
        await self._audit(school_id, user_id, "FEE_STRUCTURE_CREATED", "fee_structure", s.id)
        await self.db.commit()
        return await self.get_structure(school_id, s.id)

    async def update_structure(
        self, school_id: UUID, user_id: UUID, structure_id: UUID, data: FeeStructureUpdate
    ) -> FeeStructureOut:
        s = await self.db.get(FeeStructure, structure_id)
        if not s or s.school_id != school_id:
            raise NotFoundError("Fee structure not found")
        if s.status == FeeStructureStatus.PUBLISHED.value and data.name:
            # allow limited edits
            pass
        for field in ("name", "grade_id", "section_id", "charge_model", "active", "effective_from", "effective_to"):
            val = getattr(data, field)
            if val is not None:
                setattr(s, field, val.strip() if field == "name" and isinstance(val, str) else val)
        await self._audit(school_id, user_id, "FEE_STRUCTURE_UPDATED", "fee_structure", s.id)
        await self.db.commit()
        return await self.get_structure(school_id, s.id)

    async def publish_structure(
        self, school_id: UUID, user_id: UUID, structure_id: UUID
    ) -> FeeStructureOut:
        s = (
            await self.db.execute(
                select(FeeStructure)
                .where(FeeStructure.id == structure_id, FeeStructure.school_id == school_id)
                .options(selectinload(FeeStructure.items))
            )
        ).scalar_one_or_none()
        if not s:
            raise NotFoundError("Fee structure not found")
        if not s.items:
            raise AppError("Add fee components before publishing", status_code=422)
        s.status = FeeStructureStatus.PUBLISHED.value
        s.active = True
        await self._audit(school_id, user_id, "FEE_STRUCTURE_PUBLISHED", "fee_structure", s.id)
        await self.db.commit()
        return await self.get_structure(school_id, s.id)

    async def add_installment(
        self, school_id: UUID, structure_id: UUID, data: InstallmentIn
    ) -> InstallmentOut:
        s = await self.db.get(FeeStructure, structure_id)
        if not s or s.school_id != school_id:
            raise NotFoundError("Fee structure not found")
        row = FeeInstallment(
            id=uuid4(),
            school_id=school_id,
            fee_structure_id=structure_id,
            name=data.name.strip(),
            due_date=data.due_date,
            sequence=data.sequence,
            amount=money(data.amount),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return InstallmentOut.model_validate(row)

    async def add_structure_item(
        self, school_id: UUID, structure_id: UUID, data: StructureItemIn
    ) -> StructureItemOut:
        s = await self.db.get(FeeStructure, structure_id)
        if not s or s.school_id != school_id:
            raise NotFoundError("Fee structure not found")
        cat = await self.db.get(FeeCategory, data.fee_category_id)
        if not cat or cat.school_id != school_id:
            raise NotFoundError("Category not found")
        row = FeeStructureItem(
            id=uuid4(),
            school_id=school_id,
            fee_structure_id=structure_id,
            fee_category_id=data.fee_category_id,
            amount=money(data.amount),
            taxable=data.taxable,
            refundable=data.refundable,
            mandatory=data.mandatory,
            description=data.description,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return StructureItemOut(
            id=row.id,
            fee_category_id=row.fee_category_id,
            amount=money(row.amount),
            taxable=row.taxable,
            refundable=row.refundable,
            mandatory=row.mandatory,
            description=row.description,
            category_name=cat.name,
            category_code=cat.code,
        )

    # ── accounts / adjustments ────────────────────────────────

    async def ensure_account(
        self,
        school_id: UUID,
        student_id: UUID,
        academic_year_id: UUID,
        fee_structure_id: UUID | None,
        enrollment_id: UUID | None = None,
    ) -> StudentFeeAccount:
        row = (
            await self.db.execute(
                select(StudentFeeAccount).where(
                    StudentFeeAccount.school_id == school_id,
                    StudentFeeAccount.student_id == student_id,
                    StudentFeeAccount.academic_year_id == academic_year_id,
                )
            )
        ).scalar_one_or_none()
        if row:
            if fee_structure_id and not row.fee_structure_id:
                row.fee_structure_id = fee_structure_id
            return row
        row = StudentFeeAccount(
            id=uuid4(),
            school_id=school_id,
            student_id=student_id,
            academic_year_id=academic_year_id,
            fee_structure_id=fee_structure_id,
            enrollment_id=enrollment_id,
            status=StudentFeeAccountStatus.ACTIVE.value,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def add_adjustment(
        self, school_id: UUID, user_id: UUID, account_id: UUID, data: AdjustmentCreate
    ) -> AdjustmentOut:
        acc = await self.db.get(StudentFeeAccount, account_id)
        if not acc or acc.school_id != school_id:
            raise NotFoundError("Fee account not found")
        row = FeeAdjustment(
            id=uuid4(),
            school_id=school_id,
            student_fee_account_id=account_id,
            fee_category_id=data.fee_category_id,
            type=data.type,
            amount=money(data.amount),
            reason=data.reason.strip(),
            created_by=user_id,
            approved_by=user_id,
        )
        self.db.add(row)
        await self._audit(
            school_id, user_id, "DISCOUNT_APPLIED", "fee_adjustment", row.id, reason=data.reason
        )
        await self.db.commit()
        await self.db.refresh(row)
        return AdjustmentOut.model_validate(row)

    async def _student_name(self, student_id: UUID) -> str:
        st = await self.db.get(Student, student_id)
        if not st:
            return "Student"
        user = await self.db.get(User, st.user_id)
        return user.full_name if user else "Student"

    async def _class_name(self, class_id: UUID | None) -> str | None:
        if not class_id:
            return None
        c = await self.db.get(ClassRoom, class_id)
        if not c:
            return None
        return f"{c.name}{('-' + c.section) if c.section else ''}"

    async def _invoice_out(self, inv: FeeInvoice) -> InvoiceOut:
        acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
        student_id = acc.student_id if acc else None
        class_name = None
        if acc and acc.enrollment_id:
            enr = await self.db.get(Enrollment, acc.enrollment_id)
            class_name = await self._class_name(enr.class_id) if enr else None
        items = (
            await self.db.execute(
                select(FeeInvoiceItem).where(FeeInvoiceItem.invoice_id == inv.id)
            )
        ).scalars().all()
        return InvoiceOut(
            id=inv.id,
            school_id=inv.school_id,
            student_fee_account_id=inv.student_fee_account_id,
            student_id=student_id,
            student_name=await self._student_name(student_id) if student_id else None,
            class_name=class_name,
            installment_id=inv.installment_id,
            invoice_number=inv.invoice_number,
            invoice_date=inv.invoice_date,
            due_date=inv.due_date,
            currency=inv.currency,
            subtotal=money(inv.subtotal),
            discount_amount=money(inv.discount_amount),
            tax_amount=money(inv.tax_amount),
            total_amount=money(inv.total_amount),
            paid_amount=money(inv.paid_amount),
            balance_amount=money(inv.balance_amount),
            status=inv.status,
            items=[InvoiceItemOut.model_validate(i) for i in items],
        )

    async def generate_invoices(
        self, school_id: UUID, user_id: UUID, data: InvoiceGenerateIn
    ) -> GenerateResultOut:
        structure = (
            await self.db.execute(
                select(FeeStructure)
                .where(
                    FeeStructure.id == data.fee_structure_id,
                    FeeStructure.school_id == school_id,
                )
                .options(
                    selectinload(FeeStructure.items),
                    selectinload(FeeStructure.installments),
                )
            )
        ).scalar_one_or_none()
        if not structure:
            raise NotFoundError("Fee structure not found")
        if structure.status != FeeStructureStatus.PUBLISHED.value:
            raise AppError("Publish fee structure before generating invoices", status_code=422)

        installment = None
        if data.installment_id:
            installment = await self.db.get(FeeInstallment, data.installment_id)
            if not installment or installment.fee_structure_id != structure.id:
                raise NotFoundError("Installment not found")

        settings = await self.get_or_create_settings(school_id)
        invoice_date = data.invoice_date or date.today()
        due_date = data.due_date or (installment.due_date if installment else invoice_date)

        # Resolve students via enrollment
        q = select(Enrollment).where(
            Enrollment.school_id == school_id,
            Enrollment.academic_year_id == data.academic_year_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
        )
        if data.section_id:
            q = q.where(Enrollment.class_id == data.section_id)
        elif data.grade_id:
            q = q.where(Enrollment.grade_id == data.grade_id)
        elif structure.section_id:
            q = q.where(Enrollment.class_id == structure.section_id)
        elif structure.grade_id:
            q = q.where(Enrollment.grade_id == structure.grade_id)
        enrollments = (await self.db.execute(q)).scalars().all()
        if data.student_ids:
            wanted = set(data.student_ids)
            enrollments = [e for e in enrollments if e.student_id in wanted]

        created = 0
        skipped = 0
        total_amount = money(0)
        outs: list[InvoiceOut] = []

        structure_total = sum((money(i.amount) for i in structure.items), money(0))
        for enr in enrollments:
            acc = await self.ensure_account(
                school_id,
                enr.student_id,
                data.academic_year_id,
                structure.id,
                enr.id,
            )
            if data.installment_id:
                existing = (
                    await self.db.execute(
                        select(FeeInvoice).where(
                            FeeInvoice.student_fee_account_id == acc.id,
                            FeeInvoice.installment_id == data.installment_id,
                            FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    skipped += 1
                    continue

            adjustments = (
                await self.db.execute(
                    select(FeeAdjustment).where(FeeAdjustment.student_fee_account_id == acc.id)
                )
            ).scalars().all()
            adj_total = sum((money(a.amount) for a in adjustments), money(0))

            if installment:
                base = money(installment.amount)
                # Proportion of structure total discount
                share = (base / structure_total) if structure_total > 0 else money(1)
                discount = money(adj_total * share)
                # Build single line or distribute structure items proportionally
                items_spec = [
                    (
                        None,
                        installment.name,
                        base,
                    )
                ]
            else:
                base = structure_total
                discount = money(adj_total)
                items_spec = []
                for it in structure.items:
                    cat = await self.db.get(FeeCategory, it.fee_category_id)
                    items_spec.append(
                        (it.fee_category_id, cat.name if cat else "Fee", money(it.amount))
                    )

            if discount > base:
                discount = base
            total = money(base - discount)
            number = await self.next_number(school_id, "INVOICE", settings.invoice_prefix)
            inv = FeeInvoice(
                id=uuid4(),
                school_id=school_id,
                student_fee_account_id=acc.id,
                installment_id=data.installment_id,
                invoice_number=number,
                invoice_date=invoice_date,
                due_date=due_date,
                currency=structure.currency or settings.currency,
                subtotal=base,
                discount_amount=discount,
                tax_amount=money(0),
                total_amount=total,
                paid_amount=money(0),
                balance_amount=total,
                status=FeeInvoiceStatus.ISSUED.value,
                created_by=user_id,
            )
            self.db.add(inv)
            await self.db.flush()

            if installment:
                self.db.add(
                    FeeInvoiceItem(
                        id=uuid4(),
                        school_id=school_id,
                        invoice_id=inv.id,
                        fee_category_id=None,
                        description=installment.name,
                        quantity=money(1),
                        unit_amount=base,
                        discount_amount=discount,
                        tax_amount=money(0),
                        total_amount=total,
                    )
                )
            else:
                remaining_discount = discount
                for idx, (cat_id, desc, amt) in enumerate(items_spec):
                    line_disc = money(0)
                    if remaining_discount > 0:
                        if idx == len(items_spec) - 1:
                            line_disc = remaining_discount
                        else:
                            line_disc = money(min(remaining_discount, amt * (discount / base) if base else 0))
                        remaining_discount = money(remaining_discount - line_disc)
                    self.db.add(
                        FeeInvoiceItem(
                            id=uuid4(),
                            school_id=school_id,
                            invoice_id=inv.id,
                            fee_category_id=cat_id,
                            description=desc,
                            quantity=money(1),
                            unit_amount=amt,
                            discount_amount=line_disc,
                            tax_amount=money(0),
                            total_amount=money(amt - line_disc),
                        )
                    )

            await self._audit(school_id, user_id, "INVOICE_CREATED", "fee_invoice", inv.id)
            from src.modules.fees.ledger import post_ledger_entry
            from src.shared.enums import LedgerEntryType

            await post_ledger_entry(
                self.db,
                school_id=school_id,
                student_id=enr.student_id,
                account_id=acc.id,
                entry_type=LedgerEntryType.DEMAND.value,
                debit=total,
                credit=money(0),
                ref_type="fee_invoice",
                ref_id=inv.id,
                narration=f"Invoice {inv.invoice_number}",
                actor_user_id=user_id,
                entry_date=invoice_date,
            )
            await self._notify_invoice(school_id, enr.student_id, inv)
            created += 1
            total_amount += total
            outs.append(await self._invoice_out(inv))

        await self.db.commit()
        return GenerateResultOut(
            created=created, skipped=skipped, total_amount=total_amount, invoices=outs
        )

    async def _notify_invoice(self, school_id: UUID, student_id: UUID, inv: FeeInvoice) -> None:
        name = await self._student_name(student_id)
        body = f"{name}: invoice {inv.invoice_number} for {inv.currency} {inv.total_amount} due {inv.due_date}."
        links = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.student_id == student_id,
                )
            )
        ).scalars().all()
        for link in links:
            parent = await self.db.get(Parent, link.parent_id)
            if not parent:
                continue
            await create_in_app_notification(
                self.db,
                school_id=school_id,
                user_id=parent.user_id,
                title="New fee invoice",
                body=body,
                category=NotificationType.FEE_INVOICE_ISSUED.value,
                link_path=f"/parent/fees/invoices/{inv.id}",
                entity_type="fee_invoice",
                entity_id=inv.id,
                priority="HIGH",
                event_key=f"FEE_INVOICE_ISSUED:{inv.id}:{parent.user_id}",
            )

    async def send_invoice_reminders(self, school_id: UUID) -> dict:
        """
        Daily job: scan invoices and send due/overdue reminders (in-app + SMS/WhatsApp),
        deduped via `fee_reminder_logs`.
        """
        today = date.today()
        due_offsets = {3, 0}  # days before due date
        overdue_offsets = {1, 3, 7, 14, 30}  # days after due date

        max_window = max(due_offsets) if due_offsets else 0
        rows = (
            await self.db.execute(
                select(FeeInvoice)
                .where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.balance_amount > Decimal("0"),
                    FeeInvoice.status.in_(
                        [
                            FeeInvoiceStatus.ISSUED.value,
                            FeeInvoiceStatus.PARTIALLY_PAID.value,
                            FeeInvoiceStatus.OVERDUE.value,
                        ]
                    ),
                    FeeInvoice.due_date <= (today + timedelta(days=max_window)),
                )
                .order_by(FeeInvoice.due_date.asc())
            )
        ).scalars().all()

        invoices_considered = 0
        invoice_events_sent = 0
        deliveries_queued = 0

        for inv in rows:
            invoices_considered += 1
            days_until = (inv.due_date - today).days
            notif_type: str | None = None
            kind: str | None = None
            title: str | None = None

            if days_until in due_offsets:
                notif_type = NotificationType.FEE_DUE_REMINDER.value
                kind = f"DUE_{days_until}"
                title = "Fee due reminder"
            elif days_until < 0:
                days_overdue = abs(days_until)
                if days_overdue in overdue_offsets:
                    notif_type = NotificationType.FEE_OVERDUE.value
                    kind = f"OVERDUE_{days_overdue}"
                    title = "Fee overdue reminder"

            if not notif_type or not kind:
                continue

            existing = (
                await self.db.execute(
                    select(FeeReminderLog).where(
                        FeeReminderLog.invoice_id == inv.id,
                        FeeReminderLog.kind == kind,
                        FeeReminderLog.sent_on == today,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue

            acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
            student_id = acc.student_id if acc else None
            if not student_id:
                continue

            # record dedupe marker first
            self.db.add(FeeReminderLog(school_id=school_id, invoice_id=inv.id, kind=kind, sent_on=today))
            await self.db.flush()

            student_name = await self._student_name(student_id)
            if notif_type == NotificationType.FEE_DUE_REMINDER.value:
                body = (
                    f"{student_name}: invoice {inv.invoice_number} of {inv.currency} {inv.balance_amount} "
                    f"is due on {inv.due_date}."
                )
            else:
                body = (
                    f"{student_name}: invoice {inv.invoice_number} of {inv.currency} {inv.balance_amount} "
                    f"is overdue since {inv.due_date}."
                )
            link_path = f"/parent/fees/invoices/{inv.id}"

            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == school_id,
                        StudentParent.student_id == student_id,
                    )
                )
            ).scalars().all()

            any_recipient = False
            for link in links:
                parent = await self.db.get(Parent, link.parent_id)
                if not parent:
                    continue
                any_recipient = True

                await create_in_app_notification(
                    self.db,
                    school_id=school_id,
                    user_id=parent.user_id,
                    title=title or "Fee reminder",
                    body=body,
                    category=notif_type,
                    link_path=link_path,
                    entity_type="fee_invoice",
                    entity_id=inv.id,
                    priority="NORMAL",
                    event_key=f"{notif_type}:{inv.id}:{kind}:{parent.user_id}",
                )

            event_key = "fee_due" if notif_type == NotificationType.FEE_DUE_REMINDER.value else "fee_overdue"
            if any_recipient:
                from src.modules.alerts.service import dispatch_alert, school_display_name

                try:
                    alert = await dispatch_alert(
                        self.db,
                        school_id=school_id,
                        event_key=event_key,
                        notification_type=notif_type,
                        student_id=student_id,
                        variables={
                            "student_name": student_name,
                            "amount": f"{inv.currency} {inv.balance_amount}",
                            "due_date": str(inv.due_date),
                            "invoice_number": inv.invoice_number,
                            "school_name": await school_display_name(self.db, school_id),
                        },
                        dedupe_key=f"{event_key}:{inv.id}:{kind}:{today.isoformat()}",
                        title=title,
                        link_path=link_path,
                        urgent=False,
                    )
                    deliveries_queued += alert.queued + alert.scheduled
                except Exception:
                    from src.core.telemetry import track_alert

                    track_alert("fee_alert_failed", invoice_id=str(inv.id))

            if any_recipient:
                invoice_events_sent += 1

        await self.db.commit()
        return {
            "invoices_considered": invoices_considered,
            "invoice_events_sent": invoice_events_sent,
            "deliveries_queued": deliveries_queued,
        }

    async def list_invoices(
        self,
        school_id: UUID,
        *,
        status: str | None = None,
        student_id: UUID | None = None,
    ) -> list[InvoiceOut]:
        q = select(FeeInvoice).where(FeeInvoice.school_id == school_id)
        if status:
            q = q.where(FeeInvoice.status == status)
        if student_id:
            acc_ids = (
                await self.db.execute(
                    select(StudentFeeAccount.id).where(
                        StudentFeeAccount.school_id == school_id,
                        StudentFeeAccount.student_id == student_id,
                    )
                )
            ).scalars().all()
            q = q.where(FeeInvoice.student_fee_account_id.in_(acc_ids or [uuid4()]))
        rows = (
            await self.db.execute(q.order_by(FeeInvoice.due_date.desc()))
        ).scalars().all()
        return [await self._invoice_out(r) for r in rows]

    async def get_invoice(self, school_id: UUID, invoice_id: UUID) -> InvoiceOut:
        inv = await self.db.get(FeeInvoice, invoice_id)
        if not inv or inv.school_id != school_id:
            raise NotFoundError("Invoice not found")
        return await self._invoice_out(inv)

    async def cancel_invoice(
        self, school_id: UUID, user_id: UUID, invoice_id: UUID, reason: str
    ) -> InvoiceOut:
        inv = await self.db.get(FeeInvoice, invoice_id)
        if not inv or inv.school_id != school_id:
            raise NotFoundError("Invoice not found")
        if money(inv.paid_amount) > 0:
            raise AppError("Cannot cancel invoice with payments; use refund", status_code=422)
        inv.status = FeeInvoiceStatus.CANCELLED.value
        inv.cancelled_at = _utcnow()
        inv.cancel_reason = reason
        await self._audit(
            school_id, user_id, "INVOICE_CANCELLED", "fee_invoice", inv.id, reason=reason
        )
        await self.db.commit()
        return await self._invoice_out(inv)

    async def refresh_overdue(self, school_id: UUID) -> int:
        today = date.today()
        rows = (
            await self.db.execute(
                select(FeeInvoice).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.status.in_(
                        [
                            FeeInvoiceStatus.ISSUED.value,
                            FeeInvoiceStatus.PARTIALLY_PAID.value,
                            FeeInvoiceStatus.OVERDUE.value,
                        ]
                    ),
                    FeeInvoice.due_date < today,
                    FeeInvoice.balance_amount > 0,
                )
            )
        ).scalars().all()
        n = 0
        for inv in rows:
            if inv.status != FeeInvoiceStatus.OVERDUE.value:
                inv.status = FeeInvoiceStatus.OVERDUE.value
                n += 1
        await self.db.commit()
        return n

    async def apply_late_fees(self, school_id: UUID, user_id: UUID) -> int:
        """Add late-fee invoice items once per overdue invoice when rules match."""
        rules = (
            await self.db.execute(
                select(LateFeeRule).where(
                    LateFeeRule.school_id == school_id, LateFeeRule.active.is_(True)
                )
            )
        ).scalars().all()
        if not rules:
            return 0
        today = date.today()
        invoices = (
            await self.db.execute(
                select(FeeInvoice).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.status.in_(
                        [
                            FeeInvoiceStatus.ISSUED.value,
                            FeeInvoiceStatus.PARTIALLY_PAID.value,
                            FeeInvoiceStatus.OVERDUE.value,
                        ]
                    ),
                )
            )
        ).scalars().all()
        applied = 0
        for inv in invoices:
            existing_late = (
                await self.db.execute(
                    select(FeeInvoiceItem).where(
                        FeeInvoiceItem.invoice_id == inv.id,
                        FeeInvoiceItem.description.ilike("%late fee%"),
                    ).limit(1)
                )
            ).scalars().first()
            if existing_late:
                continue
            rule = rules[0]
            slabs = json.loads(rule.slabs_json) if getattr(rule, "slabs_json", None) else None
            fee = calculate_late_fee(
                balance=money(inv.balance_amount),
                due_date=inv.due_date,
                as_of=today,
                grace_days=rule.grace_days,
                calculation_type=rule.calculation_type,
                amount=money(rule.amount),
                maximum_amount=money(rule.maximum_amount) if rule.maximum_amount is not None else None,
                slabs=slabs,
            )
            if fee <= 0:
                continue
            self.db.add(
                FeeInvoiceItem(
                    id=uuid4(),
                    school_id=school_id,
                    invoice_id=inv.id,
                    fee_category_id=rule.fee_category_id,
                    description="Late fee",
                    quantity=money(1),
                    unit_amount=fee,
                    discount_amount=money(0),
                    tax_amount=money(0),
                    total_amount=fee,
                )
            )
            inv.subtotal = money(inv.subtotal) + fee
            inv.total_amount = money(inv.total_amount) + fee
            inv.balance_amount = money(inv.balance_amount) + fee
            inv.status = invoice_status_after_payment(
                total=money(inv.total_amount),
                paid=money(inv.paid_amount),
                due_date=inv.due_date,
            )
            acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
            if acc and acc.school_id == school_id:
                from src.modules.fees.ledger import post_ledger_entry
                from src.shared.enums import LedgerEntryType

                await post_ledger_entry(
                    self.db,
                    school_id=school_id,
                    student_id=acc.student_id,
                    account_id=acc.id,
                    entry_type=LedgerEntryType.FINE.value,
                    debit=fee,
                    credit=money(0),
                    ref_type="fee_invoice",
                    ref_id=inv.id,
                    narration=f"Late fee on {inv.invoice_number}",
                    actor_user_id=user_id,
                    entry_date=today,
                )
            applied += 1
        if applied:
            await self._audit(school_id, user_id, "LATE_FEE_APPLIED", "fee_invoice", None)
            await self.db.commit()
        return applied

    # ── parent / student views ────────────────────────────────

    async def _linked_student_ids(self, school_id: UUID, user_id: UUID, role: str) -> list[UUID]:
        if role == UserRole.PARENT.value:
            parent = (
                await self.db.execute(
                    select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
                )
            ).scalar_one_or_none()
            if not parent:
                return []
            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == school_id,
                        StudentParent.parent_id == parent.id,
                    )
                )
            ).scalars().all()
            return [l.student_id for l in links]
        if role == UserRole.STUDENT.value:
            # Read-only: a student session may not INSERT fee_settings under RLS, so a
            # school without a settings row means the default (student viewing off).
            settings = (
                await self.db.execute(select(FeeSettings).where(FeeSettings.school_id == school_id))
            ).scalar_one_or_none()
            if settings is None or not settings.allow_student_view_fees:
                raise ForbiddenError("Student fee viewing is disabled")
            st = (
                await self.db.execute(
                    select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
                )
            ).scalar_one_or_none()
            return [st.id] if st else []
        if role == UserRole.SCHOOL_ADMIN.value:
            rows = (
                await self.db.execute(select(Student.id).where(Student.school_id == school_id))
            ).scalars().all()
            return list(rows)
        raise ForbiddenError("Not allowed")

    async def student_summary(
        self, school_id: UUID, student_id: UUID, academic_year_id: UUID | None = None
    ) -> StudentFeeSummaryOut:
        q = select(StudentFeeAccount).where(
            StudentFeeAccount.school_id == school_id,
            StudentFeeAccount.student_id == student_id,
        )
        if academic_year_id:
            q = q.where(StudentFeeAccount.academic_year_id == academic_year_id)
        accounts = (await self.db.execute(q)).scalars().all()
        assessed = money(0)
        paid = money(0)
        outstanding = money(0)
        overdue = money(0)
        next_due = None
        next_amt = None
        class_name = None
        account_id = accounts[0].id if accounts else None
        ay = accounts[0].academic_year_id if accounts else None
        for acc in accounts:
            if acc.enrollment_id:
                enr = await self.db.get(Enrollment, acc.enrollment_id)
                class_name = await self._class_name(enr.class_id) if enr else class_name
            invoices = (
                await self.db.execute(
                    select(FeeInvoice).where(
                        FeeInvoice.student_fee_account_id == acc.id,
                        FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                    )
                )
            ).scalars().all()
            for inv in invoices:
                assessed += money(inv.total_amount)
                paid += money(inv.paid_amount)
                bal = money(inv.balance_amount)
                outstanding += bal
                if bal > 0 and inv.due_date < date.today():
                    overdue += bal
                if bal > 0 and (next_due is None or inv.due_date < next_due):
                    next_due = inv.due_date
                    next_amt = bal
        status = "Paid" if outstanding <= 0 and assessed > 0 else ("Overdue" if overdue > 0 else ("Due" if outstanding > 0 else "None"))
        return StudentFeeSummaryOut(
            student_id=student_id,
            student_name=await self._student_name(student_id),
            class_name=class_name,
            academic_year_id=ay,
            account_id=account_id,
            assessed=assessed,
            paid=paid,
            outstanding=outstanding,
            overdue=overdue,
            next_due_date=next_due,
            next_due_amount=next_amt,
            status=status,
        )

    async def list_student_summaries(
        self,
        school_id: UUID,
        *,
        academic_year_id: UUID | None = None,
        grade_id: UUID | None = None,
        section_id: UUID | None = None,
    ) -> list[StudentFeeSummaryOut]:
        q = select(Enrollment).where(
            Enrollment.school_id == school_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
        )
        if academic_year_id:
            q = q.where(Enrollment.academic_year_id == academic_year_id)
        if grade_id:
            q = q.where(Enrollment.grade_id == grade_id)
        if section_id:
            q = q.where(Enrollment.class_id == section_id)
        enrollments = (await self.db.execute(q)).scalars().all()
        out = []
        for enr in enrollments:
            out.append(
                await self.student_summary(school_id, enr.student_id, enr.academic_year_id)
            )
        return out

    async def ledger(self, school_id: UUID, student_id: UUID) -> list[LedgerEntryOut]:
        accounts = (
            await self.db.execute(
                select(StudentFeeAccount).where(
                    StudentFeeAccount.school_id == school_id,
                    StudentFeeAccount.student_id == student_id,
                )
            )
        ).scalars().all()
        events: list[tuple[date, str, Decimal, Decimal]] = []
        for acc in accounts:
            invoices = (
                await self.db.execute(
                    select(FeeInvoice).where(FeeInvoice.student_fee_account_id == acc.id)
                )
            ).scalars().all()
            for inv in invoices:
                if inv.status == FeeInvoiceStatus.CANCELLED.value:
                    continue
                events.append(
                    (inv.invoice_date, f"Invoice {inv.invoice_number}", money(inv.total_amount), money(0))
                )
            adjs = (
                await self.db.execute(
                    select(FeeAdjustment).where(FeeAdjustment.student_fee_account_id == acc.id)
                )
            ).scalars().all()
            for a in adjs:
                events.append(
                    (a.created_at.date(), f"{a.type}: {a.reason[:40]}", money(0), money(a.amount))
                )
            pays = (
                await self.db.execute(
                    select(Payment).where(
                        Payment.student_fee_account_id == acc.id,
                        Payment.status == PaymentStatus.SUCCESS.value,
                    )
                )
            ).scalars().all()
            for p in pays:
                events.append(
                    (
                        (p.paid_at or p.created_at).date(),
                        f"Payment {p.payment_number}",
                        money(0),
                        money(p.amount),
                    )
                )
        events.sort(key=lambda x: x[0])
        bal = money(0)
        out: list[LedgerEntryOut] = []
        for d, desc, debit, credit in events:
            bal = money(bal + debit - credit)
            out.append(
                LedgerEntryOut(date=d, description=desc, debit=debit, credit=credit, balance=bal)
            )
        return out

    async def dashboard(self, school_id: UUID) -> FeesDashboardOut:
        settings = await self.get_or_create_settings(school_id)
        invoices = (
            await self.db.execute(
                select(FeeInvoice).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
            )
        ).scalars().all()
        assessed = sum((money(i.total_amount) for i in invoices), money(0))
        collected = sum((money(i.paid_amount) for i in invoices), money(0))
        outstanding = sum((money(i.balance_amount) for i in invoices), money(0))
        overdue = sum(
            (
                money(i.balance_amount)
                for i in invoices
                if money(i.balance_amount) > 0 and i.due_date < date.today()
            ),
            money(0),
        )
        today = date.today()
        pays = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.school_id == school_id,
                    Payment.status == PaymentStatus.SUCCESS.value,
                )
                .order_by(Payment.paid_at.desc())
                .limit(10)
            )
        ).scalars().all()
        today_collection = money(0)
        recent: list[PaymentOut] = []
        for p in pays:
            if p.paid_at and p.paid_at.date() == today:
                today_collection += money(p.amount)
            receipt = (
                await self.db.execute(
                    select(FeeReceipt).where(FeeReceipt.payment_id == p.id)
                )
            ).scalar_one_or_none()
            recent.append(
                PaymentOut(
                    id=p.id,
                    school_id=p.school_id,
                    student_fee_account_id=p.student_fee_account_id,
                    invoice_id=p.invoice_id,
                    payment_number=p.payment_number,
                    amount=money(p.amount),
                    currency=p.currency,
                    method=p.method,
                    status=p.status,
                    gateway=p.gateway,
                    gateway_order_id=p.gateway_order_id,
                    gateway_payment_id=p.gateway_payment_id,
                    gateway_reference=p.gateway_reference,
                    paid_at=p.paid_at,
                    notes=p.notes,
                    receipt_id=receipt.id if receipt else None,
                    receipt_number=receipt.receipt_number if receipt else None,
                )
            )
        return FeesDashboardOut(
            currency=settings.currency,
            assessed=assessed,
            collected=collected,
            outstanding=outstanding,
            overdue=overdue,
            today_collection=today_collection,
            recent_payments=recent,
        )

    async def get_settings(self, school_id: UUID) -> FeeSettingsOut:
        s = await self.get_or_create_settings(school_id)
        await self.db.commit()
        return FeeSettingsOut.model_validate(s)

    async def update_settings(
        self, school_id: UUID, data: FeeSettingsUpdate
    ) -> FeeSettingsOut:
        s = await self.get_or_create_settings(school_id)
        for field in (
            "currency",
            "allow_student_view_fees",
            "allow_partial_payments",
            "gateway_provider",
            "gateway_mode",
            "gateway_public_key",
        ):
            val = getattr(data, field)
            if val is not None:
                setattr(s, field, val)
        await self.db.commit()
        await self.db.refresh(s)
        return FeeSettingsOut.model_validate(s)

    async def list_late_rules(self, school_id: UUID) -> list[LateFeeRuleOut]:
        rows = (
            await self.db.execute(
                select(LateFeeRule).where(LateFeeRule.school_id == school_id)
            )
        ).scalars().all()
        return [LateFeeRuleOut.model_validate(r) for r in rows]

    async def upsert_late_rule(
        self, school_id: UUID, data: LateFeeRuleIn, rule_id: UUID | None = None
    ) -> LateFeeRuleOut:
        if rule_id:
            row = await self.db.get(LateFeeRule, rule_id)
            if not row or row.school_id != school_id:
                raise NotFoundError("Late fee rule not found")
        else:
            row = LateFeeRule(id=uuid4(), school_id=school_id)
            self.db.add(row)
        row.fee_category_id = data.fee_category_id
        row.grace_days = data.grace_days
        row.calculation_type = data.calculation_type
        row.amount = money(data.amount)
        row.maximum_amount = money(data.maximum_amount) if data.maximum_amount is not None else None
        row.slabs_json = json.dumps(data.slabs) if data.slabs else None
        row.active = data.active
        await self.db.commit()
        await self.db.refresh(row)
        out = LateFeeRuleOut.model_validate(row)
        out.slabs = json.loads(row.slabs_json) if row.slabs_json else None
        return out

    async def report_collection(
        self, school_id: UUID, *, from_date: date | None = None, to_date: date | None = None
    ) -> list[CollectionRowOut]:
        q = select(Payment).where(
            Payment.school_id == school_id, Payment.status == PaymentStatus.SUCCESS.value
        )
        rows = (await self.db.execute(q)).scalars().all()
        buckets: dict[tuple[date, str], Decimal] = {}
        for p in rows:
            d = (p.paid_at or p.created_at).date()
            if from_date and d < from_date:
                continue
            if to_date and d > to_date:
                continue
            key = (d, p.method)
            buckets[key] = money(buckets.get(key, 0) + money(p.amount))
        return [
            CollectionRowOut(date=k[0], method=k[1], total=v)
            for k, v in sorted(buckets.items(), key=lambda x: x[0][0])
        ]

    async def report_outstanding(self, school_id: UUID) -> list[OutstandingRowOut]:
        invoices = (
            await self.db.execute(
                select(FeeInvoice).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
            )
        ).scalars().all()
        out = []
        today = date.today()
        for inv in invoices:
            detail = await self._invoice_out(inv)
            days = max((today - inv.due_date).days, 0) if inv.due_date < today else 0
            out.append(
                OutstandingRowOut(
                    student_id=detail.student_id or uuid4(),
                    student_name=detail.student_name or "Student",
                    class_name=detail.class_name,
                    invoice_number=inv.invoice_number,
                    due_date=inv.due_date,
                    total_amount=money(inv.total_amount),
                    paid_amount=money(inv.paid_amount),
                    balance_amount=money(inv.balance_amount),
                    status=inv.status,
                    days_overdue=days,
                )
            )
        return out

    async def report_csv(self, rows: list[dict], fieldnames: list[str]) -> str:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return buf.getvalue()
