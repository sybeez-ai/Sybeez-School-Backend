"""School books: chart, journals, funds, vendors, budgets, bank, tax, purchases, assets.

Money is rupees with two decimal places. A posted journal is balanced and is not edited;
a mistake is a new reversing journal.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LedgerAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_ledger_accounts_school_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)  # ASSET LIABILITY INCOME EXPENSE EQUITY
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class JournalEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "journal_entries"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    narration: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")  # DRAFT POSTED
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class JournalLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "journal_lines"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    narration: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SchoolFund(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "school_funds"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_school_funds_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))


class BookVendor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "book_vendors"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class SchoolBudget(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "school_budgets"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    year_label: Mapped[str] = mapped_column(String(16), nullable=False)  # 2026-27
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    account_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


class BookBankAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "book_bank_accounts"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    ifsc: Mapped[str | None] = mapped_column(String(16), nullable=True)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class BookBankLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One line from a bank statement, always tied to one school bank account."""

    __tablename__ = "book_bank_lines"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("book_bank_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    line_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)


class TaxItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tax_items"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_tax_items_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="GST")  # GST TDS


class BookPurchase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "book_purchases"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("book_vendors.id", ondelete="SET NULL"), nullable=True)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    narration: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")  # DRAFT APPROVED
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class BookAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "book_assets"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    acquired_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_USE")  # IN_USE DISPOSED


class TransportRoute(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "transport_routes"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_transport_routes_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    vehicle_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stops: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class OpsAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ops_assets"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="GENERAL")
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_USE")
    acquired_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class OpsInventoryItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ops_inventory_items"
    __table_args__ = (UniqueConstraint("school_id", "sku", name="uq_ops_inventory_sku"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="NOS")
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))


class OpsPurchaseOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ops_purchase_orders"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    vendor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")  # DRAFT ORDERED RECEIVED
    lines: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CautionDeposit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "caution_deposits"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="HELD")  # HELD REFUNDED
    refunded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(240), nullable=True)


class BookRefund(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A refund recorded in the books, for a book sale or a similar charge."""

    __tablename__ = "book_refunds"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    refund_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="REQUESTED")  # REQUESTED PAID


class TdsEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tds_entries"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    section_code: Mapped[str] = mapped_column(String(16), nullable=False, default="194C")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    deducted_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(240), nullable=True)


class GstInvoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "gst_invoices"
    __table_args__ = (UniqueConstraint("school_id", "invoice_number", name="uq_gst_invoices_number"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)
    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(240), nullable=True)


class OpsMaintenanceJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ops_maintenance_jobs"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("ops_assets.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN IN_PROGRESS DONE
    reported_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
