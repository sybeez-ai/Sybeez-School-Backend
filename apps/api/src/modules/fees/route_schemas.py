"""Request bodies for Razorpay Route onboarding and reports."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_GST = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_BUSINESS_TYPES = {
    "llp",
    "ngo",
    "individual",
    "partnership",
    "proprietorship",
    "public_limited",
    "private_limited",
    "trust",
    "society",
    "not_yet_registered",
    "educational_institutes",
    "huf",
}


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


class RouteAccountIn(BaseModel):
    email: EmailStr
    phone: str
    legal_business_name: str = Field(min_length=3, max_length=200)
    contact_name: str = Field(min_length=2, max_length=120)
    business_type: str = "educational_institutes"
    street1: str = Field(min_length=3, max_length=200)
    street2: str | None = None
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    postal_code: str = Field(min_length=6, max_length=6)
    pan: str | None = None
    gst: str | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        digits = _digits(value)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if len(digits) != 10:
            raise ValueError("Phone must be a 10-digit Indian mobile number")
        return digits

    @field_validator("business_type")
    @classmethod
    def _business(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in _BUSINESS_TYPES:
            raise ValueError("Unsupported business type")
        return name

    @field_validator("pan")
    @classmethod
    def _pan(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        pan = value.strip().upper()
        if not _PAN.match(pan):
            raise ValueError("PAN must look like AAAAA9999A")
        return pan

    @field_validator("gst")
    @classmethod
    def _gst(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        gst = value.strip().upper()
        if not _GST.match(gst):
            raise ValueError("GSTIN is not valid")
        return gst

    @field_validator("postal_code")
    @classmethod
    def _pin(cls, value: str) -> str:
        digits = _digits(value)
        if len(digits) != 6:
            raise ValueError("Postal code must be 6 digits")
        return digits


class RouteStakeholderIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    street: str = Field(min_length=3, max_length=200)
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    postal_code: str
    pan: str | None = None

    @field_validator("postal_code")
    @classmethod
    def _pin(cls, value: str) -> str:
        digits = _digits(value)
        if len(digits) != 6:
            raise ValueError("Postal code must be 6 digits")
        return digits

    @field_validator("pan")
    @classmethod
    def _pan(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        pan = value.strip().upper()
        if not _PAN.match(pan):
            raise ValueError("PAN must look like AAAAA9999A")
        return pan


class RouteSettlementIn(BaseModel):
    account_number: str
    ifsc_code: str
    beneficiary_name: str = Field(min_length=2, max_length=120)
    tnc_accepted: bool

    @field_validator("account_number")
    @classmethod
    def _account(cls, value: str) -> str:
        digits = _digits(value)
        if not 9 <= len(digits) <= 18:
            raise ValueError("Account number must be 9 to 18 digits")
        return digits

    @field_validator("ifsc_code")
    @classmethod
    def _ifsc(cls, value: str) -> str:
        code = value.strip().upper()
        if not _IFSC.match(code):
            raise ValueError("IFSC must look like HDFC0000001")
        return code

    @field_validator("tnc_accepted")
    @classmethod
    def _tnc(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Route terms must be accepted")
        return value


class RouteStatusOut(BaseModel):
    school_id: UUID
    linked_account_id: str | None = None
    route_product_id: str | None = None
    stakeholder_id: str | None = None
    route_status: str
    platform_fee_percent: Decimal
    platform_fee_fixed: Decimal | None = None
    settlement_on_hold: bool
    settlement_hold_until: datetime | None = None
    allow_platform_settlement: bool
    bank_account_last4: str | None = None
    bank_ifsc: str | None = None
    beneficiary_name: str | None = None
    legal_business_name: str | None = None
    online_payments: str


class PlatformRouteUpdate(BaseModel):
    platform_fee_percent: Decimal | None = Field(default=None, ge=0, le=100)
    platform_fee_fixed: Decimal | None = Field(default=None, ge=0)
    clear_platform_fee_fixed: bool = False
    allow_platform_settlement: bool | None = None
    settlement_on_hold: bool | None = None
    settlement_hold_until: datetime | None = None
    clear_settlement_hold_until: bool = False


class RouteSchoolOut(RouteStatusOut):
    school_name: str


class TransferRowOut(BaseModel):
    id: UUID
    school_id: UUID
    payment_id: UUID
    payment_number: str | None = None
    linked_account_id: str
    gateway_order_id: str | None = None
    gateway_payment_id: str | None = None
    gateway_transfer_id: str | None = None
    currency: str
    payment_amount: Decimal
    transfer_amount: Decimal
    platform_fee_amount: Decimal
    on_hold: bool
    status: str
    failure_reason: str | None = None
    settlement_id: str | None = None
    settled_at: datetime | None = None
    payment_status: str | None = None


class PlatformFeeReportOut(BaseModel):
    currency: str
    payment_count: int
    gross_collected: Decimal
    platform_fees: Decimal
    transferred: Decimal
    failed_count: int
    failed_amount: Decimal


class ReconciliationRowOut(BaseModel):
    payment_id: UUID
    payment_number: str
    payment_status: str
    payment_amount: Decimal
    platform_fee_amount: Decimal
    transfer_amount: Decimal | None = None
    expected_transfer: Decimal | None = None
    transfer_status: str | None = None
    settlement_id: str | None = None
    matched: bool
    reason: str
