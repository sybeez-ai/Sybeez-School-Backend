"""Fee collection profile (UPI payee, GST identity) and a UPI link + QR for any invoice."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.db.models import SchoolFeeProfile
from src.modules.access.audit import write_audit
from src.modules.fee_profile.upi import GSTIN_RE, VPA_RE, qr_svg, upi_uri
from src.modules.fees.permissions import FINANCE_SETUP, FINANCE_VIEW, assert_not_teacher_finance
from src.modules.fees.router import scoped_db
from src.modules.fees.service import FeeService
from src.shared.enums import UserRole

router = APIRouter(prefix="/fees", tags=["fees"])
DB = Annotated[AsyncSession, Depends(scoped_db)]


class ProfileIn(BaseModel):
    upi_vpa: str | None = Field(default=None, max_length=100)
    payee_name: str | None = Field(default=None, max_length=120)
    gstin: str | None = Field(default=None, max_length=15)
    state_code: str | None = Field(default=None, pattern=r"^\d{2}$")
    sac_code: str = Field(default="999293", pattern=r"^\d{4,8}$")
    gst_mode: str = Field(default="EXEMPT", pattern="^(EXEMPT|TAXABLE)$")
    gst_rate: Decimal = Field(default=Decimal("0"), ge=0, le=28)
    receipt_footer: str | None = Field(default=None, max_length=300)


class ProfileOut(ProfileIn):
    configured: bool = False


class UpiOut(BaseModel):
    invoice_id: UUID
    invoice_number: str
    amount: Decimal
    payee_name: str
    upi_vpa: str
    upi_uri: str
    qr_svg: str
    reference: str
    note: str


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


async def load_profile(db: AsyncSession, school_id: UUID) -> SchoolFeeProfile | None:
    return (
        await db.execute(select(SchoolFeeProfile).where(SchoolFeeProfile.school_id == school_id))
    ).scalar_one_or_none()


@router.get("/profile", response_model=ProfileOut)
async def get_profile(db: DB, user: Annotated[CurrentUser, Depends(require_roles(*FINANCE_VIEW))]):
    row = await load_profile(db, _sid(user))
    if row is None:
        return ProfileOut()
    return ProfileOut(
        upi_vpa=row.upi_vpa, payee_name=row.payee_name, gstin=row.gstin, state_code=row.state_code,
        sac_code=row.sac_code, gst_mode=row.gst_mode, gst_rate=row.gst_rate, receipt_footer=row.receipt_footer,
        configured=bool(row.upi_vpa),
    )


@router.put("/profile", response_model=ProfileOut)
async def put_profile(body: ProfileIn, db: DB, user: Annotated[CurrentUser, Depends(require_roles(*FINANCE_SETUP))]):
    vpa = (body.upi_vpa or "").strip() or None
    if vpa and not VPA_RE.match(vpa):
        raise ValidationError("UPI ID looks wrong. It should look like school@bank")
    gstin = (body.gstin or "").strip().upper() or None
    if gstin and not GSTIN_RE.match(gstin):
        raise ValidationError("GSTIN is not in the 15-character GST format")
    if body.gst_mode == "TAXABLE" and not gstin:
        raise ValidationError("Add the school's GSTIN before turning GST on")
    sid = _sid(user)
    row = await load_profile(db, sid)
    if row is None:
        row = SchoolFeeProfile(school_id=sid)
        db.add(row)
    before = {"upi_vpa": row.upi_vpa, "gstin": row.gstin, "gst_mode": row.gst_mode}
    row.upi_vpa, row.gstin = vpa, gstin
    row.payee_name = (body.payee_name or "").strip() or None
    row.state_code, row.sac_code, row.gst_mode = body.state_code, body.sac_code, body.gst_mode
    row.gst_rate = body.gst_rate if body.gst_mode == "TAXABLE" else Decimal("0")
    row.receipt_footer = (body.receipt_footer or "").strip() or None
    row.updated_by = user.id
    await write_audit(
        db, school_id=sid, actor_id=user.id, action="fees.profile_update", resource_type="school_fee_profile",
        resource_id=str(sid), message="Fee collection profile updated",
        details={"before": before, "after": {"upi_vpa": vpa, "gstin": gstin, "gst_mode": body.gst_mode}},
    )
    await db.flush()
    return ProfileOut(**body.model_dump(exclude={"upi_vpa", "gstin"}), upi_vpa=vpa, gstin=gstin, configured=bool(vpa))


@router.get("/invoices/{invoice_id}/upi", response_model=UpiOut)
async def invoice_upi(
    invoice_id: UUID,
    db: DB,
    user: Annotated[CurrentUser, Depends(require_roles(*FINANCE_VIEW, UserRole.PARENT, UserRole.STUDENT))],
):
    """UPI link and QR for the invoice's outstanding balance. Parents/students only for their own children."""
    assert_not_teacher_finance(user.role.value)
    sid = _sid(user)
    svc = FeeService(db)
    inv = await svc.get_invoice(sid, invoice_id)
    if user.role in (UserRole.PARENT, UserRole.STUDENT):
        if inv.student_id not in await svc._linked_student_ids(sid, user.id, user.role.value):
            raise ForbiddenError("Not allowed")
    profile = await load_profile(db, sid)
    if profile is None or not profile.upi_vpa:
        raise NotFoundError("This school has not set up UPI payments yet")
    balance = Decimal(str(inv.balance_amount))
    if balance <= 0:
        raise ConflictError("Nothing is due on this invoice")
    from src.db.models import School

    school = await db.get(School, sid)
    payee = profile.payee_name or (school.name if school else "School")
    note = f"Fees {inv.invoice_number}"
    uri = upi_uri(vpa=profile.upi_vpa, payee=payee, amount=balance, invoice_number=inv.invoice_number, note=note)
    return UpiOut(
        invoice_id=invoice_id, invoice_number=inv.invoice_number, amount=balance, payee_name=payee,
        upi_vpa=profile.upi_vpa, upi_uri=uri, qr_svg=qr_svg(uri), reference=inv.invoice_number, note=note,
    )
