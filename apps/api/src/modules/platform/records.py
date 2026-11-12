"""Super Admin record browser and delete.

Other roles cannot call these routes. Deletes reuse the status the table already
uses (LEFT, ARCHIVED, CANCELLED, is_active) and only hard-delete a row when the
table has no such flag. Append-only finance ledgers are left in place; the
voucher they belong to is marked rejected instead of removed.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_super_admin
from src.core.database import get_db
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.db.models import (
    Announcement,
    ClassRoom,
    Document,
    Employee,
    Enrollment,
    FeeInvoice,
    FeeSettings,
    FeeStructure,
    Homework,
    HrSettings,
    Parent,
    Payment,
    PayrollPayslip,
    PayrollRun,
    PayrollSchoolSettings,
    PlatformStaff,
    School,
    SchoolExpense,
    SchoolMembership,
    Student,
    Subject,
    Teacher,
    User,
)
from src.db.models.finance import ExpenseHead
from src.db.models.school_ops import FamilyDocument
from src.modules.audit.repository import AuditRepository
from src.modules.fees.tenant import set_rls_bypass
from src.modules.platform.service import PlatformService
from src.shared.enums import (
    AnnouncementStatus,
    EnrollmentStatus,
    FeeInvoiceStatus,
    FeeStructureStatus,
    HomeworkStatus,
    MembershipStatus,
    PaymentStatus,
    PersonStatus,
    SubjectStatus,
    UserRole,
)
from src.shared.pagination import Page

router = APIRouter(prefix="/platform/records", tags=["platform-records"])


class RecordKindOut(BaseModel):
    key: str
    label: str
    group: str
    confirm: str


class RecordOut(BaseModel):
    id: UUID
    kind: str
    label: str
    school_id: UUID | None = None
    school_name: str | None = None
    status: str | None = None
    detail: str | None = None


class RecordDeleteIn(BaseModel):
    confirm_name: str | None = None
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


KINDS: tuple[dict[str, str], ...] = (
    {"key": "schools", "label": "Schools", "group": "Schools", "confirm": "name"},
    {"key": "users", "label": "Users", "group": "People", "confirm": "ack"},
    {"key": "platform_staff", "label": "Platform staff", "group": "People", "confirm": "ack"},
    {"key": "employees", "label": "Staff", "group": "People", "confirm": "ack"},
    {"key": "teachers", "label": "Teachers", "group": "People", "confirm": "ack"},
    {"key": "students", "label": "Students", "group": "People", "confirm": "ack"},
    {"key": "parents", "label": "Parents", "group": "People", "confirm": "ack"},
    {"key": "classes", "label": "Classes", "group": "Academics", "confirm": "ack"},
    {"key": "subjects", "label": "Subjects", "group": "Academics", "confirm": "ack"},
    {"key": "homework", "label": "Homework", "group": "Academics", "confirm": "ack"},
    {"key": "announcements", "label": "Announcements", "group": "Academics", "confirm": "ack"},
    {"key": "fee_invoices", "label": "Fee invoices", "group": "Fees", "confirm": "ack"},
    {"key": "fee_payments", "label": "Fee payments", "group": "Fees", "confirm": "ack"},
    {"key": "fee_structures", "label": "Fee structures", "group": "Fees", "confirm": "ack"},
    {"key": "payroll_runs", "label": "Payroll runs", "group": "Payroll", "confirm": "ack"},
    {"key": "payslips", "label": "Payslips", "group": "Payroll", "confirm": "ack"},
    {"key": "expenses", "label": "Expenses", "group": "Finance", "confirm": "ack"},
    {"key": "documents", "label": "Documents", "group": "Documents", "confirm": "ack"},
    {"key": "family_documents", "label": "Family documents", "group": "Documents", "confirm": "ack"},
    {"key": "fee_settings", "label": "Fee settings", "group": "Settings", "confirm": "ack"},
    {"key": "payroll_settings", "label": "Payroll settings", "group": "Settings", "confirm": "ack"},
    {"key": "hr_settings", "label": "HR settings", "group": "Settings", "confirm": "ack"},
)

_BY_KEY = {item["key"]: item for item in KINDS}


def names_match(expected: str, typed: str | None) -> bool:
    return (typed or "").strip() == (expected or "").strip()


def kind_or_404(key: str) -> dict[str, str]:
    found = _BY_KEY.get(key)
    if found is None:
        raise NotFoundError("Unknown record type")
    return found


def _like(q: str | None) -> str | None:
    text = (q or "").strip()
    return f"%{text}%" if text else None


async def _audit(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    kind: str,
    record_id: UUID,
    school_id: UUID | None,
    label: str,
    mode: str,
) -> None:
    await AuditRepository(db).write(
        action="platform.super_admin.delete",
        actor_id=actor.id,
        school_id=school_id,
        resource_type=kind,
        resource_id=str(record_id),
        message=f"Super admin deleted {kind}: {label}",
        details={"mode": mode, "label": label, "kind": kind},
    )


def _page(page: int, page_size: int) -> tuple[int, int, int]:
    page = max(1, page)
    page_size = min(50, max(1, page_size))
    return page, page_size, (page - 1) * page_size


async def _list_simple(
    db: AsyncSession,
    model,
    kind: str,
    label_attr: str,
    *,
    school_id: UUID | None,
    q: str | None,
    where,
    limit: int,
    offset: int,
    status_attr: str | None = "status",
) -> tuple[list[RecordOut], int]:
    label_col = getattr(model, label_attr)
    stmt = select(model, School.name).join(School, School.id == model.school_id)
    count = select(func.count()).select_from(model).join(School, School.id == model.school_id)
    if school_id is not None:
        stmt = stmt.where(model.school_id == school_id)
        count = count.where(model.school_id == school_id)
    if where is not None:
        stmt = stmt.where(where)
        count = count.where(where)
    like = _like(q)
    if like:
        filt = or_(label_col.ilike(like), School.name.ilike(like))
        stmt = stmt.where(filt)
        count = count.where(filt)
    total = int((await db.execute(count)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(model.created_at.desc()).offset(offset).limit(limit))
    ).all()
    items = []
    for row, school_name in rows:
        status = getattr(row, status_attr, None) if status_attr else None
        if isinstance(status, bool):
            status = "active" if status else "inactive"
        items.append(
            RecordOut(
                id=row.id,
                kind=kind,
                label=str(getattr(row, label_attr) or kind),
                school_id=row.school_id,
                school_name=school_name,
                status=None if status is None else str(status),
            )
        )
    return items, total


async def list_records(
    db: AsyncSession,
    kind: str,
    *,
    school_id: UUID | None,
    q: str | None,
    page: int,
    page_size: int,
) -> tuple[list[RecordOut], int]:
    kind_or_404(kind)
    page, page_size, offset = _page(page, page_size)
    if kind == "schools":
        return await _list_schools(db, school_id, q, page_size, offset)
    if kind == "users":
        return await _list_users(db, school_id, q, page_size, offset)
    if kind == "platform_staff":
        return await _list_platform_staff(db, q, page_size, offset)
    spec = _LIST[kind]
    return await _list_simple(db, spec["model"], kind, spec["label"], school_id=school_id, q=q, where=spec["where"], limit=page_size, offset=offset, status_attr=spec.get("status"))


async def _list_schools(db, school_id, q, limit, offset):
    stmt = select(School).where(School.status != "ARCHIVED")
    count = select(func.count()).select_from(School).where(School.status != "ARCHIVED")
    if school_id is not None:
        stmt = stmt.where(School.id == school_id)
        count = count.where(School.id == school_id)
    like = _like(q)
    if like:
        filt = or_(School.name.ilike(like), School.code.ilike(like))
        stmt = stmt.where(filt)
        count = count.where(filt)
    total = int((await db.execute(count)).scalar_one())
    rows = (await db.execute(stmt.order_by(School.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return [
        RecordOut(id=row.id, kind="schools", label=row.name, school_id=row.id, school_name=row.name, status=row.status)
        for row in rows
    ], total


async def _list_users(db, school_id, q, limit, offset):
    stmt = (
        select(SchoolMembership, User, School.name)
        .join(User, User.id == SchoolMembership.user_id)
        .join(School, School.id == SchoolMembership.school_id)
        .where(SchoolMembership.status == MembershipStatus.ACTIVE.value)
    )
    count = (
        select(func.count())
        .select_from(SchoolMembership)
        .join(User, User.id == SchoolMembership.user_id)
        .join(School, School.id == SchoolMembership.school_id)
        .where(SchoolMembership.status == MembershipStatus.ACTIVE.value)
    )
    if school_id is not None:
        stmt = stmt.where(SchoolMembership.school_id == school_id)
        count = count.where(SchoolMembership.school_id == school_id)
    like = _like(q)
    if like:
        filt = or_(User.full_name.ilike(like), User.email.ilike(like), School.name.ilike(like))
        stmt = stmt.where(filt)
        count = count.where(filt)
    total = int((await db.execute(count)).scalar_one())
    rows = (await db.execute(stmt.order_by(User.full_name).offset(offset).limit(limit))).all()
    items = [
        RecordOut(
            id=mem.id,
            kind="users",
            label=f"{user.full_name} · {mem.role}",
            school_id=mem.school_id,
            school_name=school_name,
            status=mem.status,
            detail=user.email,
        )
        for mem, user, school_name in rows
    ]
    return items, total


async def _list_platform_staff(db, q, limit, offset):
    stmt = (
        select(PlatformStaff, User)
        .join(User, User.id == PlatformStaff.user_id)
        .where(PlatformStaff.status == "ACTIVE")
    )
    count = (
        select(func.count())
        .select_from(PlatformStaff)
        .join(User, User.id == PlatformStaff.user_id)
        .where(PlatformStaff.status == "ACTIVE")
    )
    like = _like(q)
    if like:
        filt = or_(User.full_name.ilike(like), User.email.ilike(like))
        stmt = stmt.where(filt)
        count = count.where(filt)
    total = int((await db.execute(count)).scalar_one())
    rows = (await db.execute(stmt.order_by(User.full_name).offset(offset).limit(limit))).all()
    return [
        RecordOut(
            id=staff.id,
            kind="platform_staff",
            label=user.full_name,
            status=staff.status,
            detail=user.email,
        )
        for staff, user in rows
    ], total


_LIST: dict[str, dict[str, Any]] = {
    "employees": {"model": Employee, "label": "full_name", "where": Employee.status == "ACTIVE", "status": "status"},
    "teachers": {"model": Teacher, "label": "employee_code", "where": Teacher.status == PersonStatus.ACTIVE.value},
    "students": {"model": Student, "label": "admission_no", "where": Student.status == PersonStatus.ACTIVE.value},
    "parents": {"model": Parent, "label": "phone", "where": Parent.status == PersonStatus.ACTIVE.value},
    "classes": {"model": ClassRoom, "label": "name", "where": None, "status": None},
    "subjects": {"model": Subject, "label": "name", "where": Subject.status != SubjectStatus.ARCHIVED.value},
    "homework": {"model": Homework, "label": "title", "where": Homework.status != HomeworkStatus.CLOSED.value},
    "announcements": {"model": Announcement, "label": "title", "where": Announcement.status != AnnouncementStatus.ARCHIVED.value},
    "fee_invoices": {"model": FeeInvoice, "label": "invoice_number", "where": FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value},
    "fee_payments": {"model": Payment, "label": "payment_number", "where": Payment.status != PaymentStatus.CANCELLED.value},
    "fee_structures": {"model": FeeStructure, "label": "name", "where": FeeStructure.status != FeeStructureStatus.ARCHIVED.value},
    "payroll_runs": {"model": PayrollRun, "label": "label", "where": PayrollRun.status != "CANCELLED"},
    "payslips": {"model": PayrollPayslip, "label": "status", "where": PayrollPayslip.status != "CANCELLED"},
    "expenses": {"model": SchoolExpense, "label": "voucher_no", "where": SchoolExpense.status != "REJECTED"},
    "documents": {"model": Document, "label": "title", "where": Document.is_active.is_(True), "status": "is_active"},
    "family_documents": {"model": FamilyDocument, "label": "title", "where": None, "status": "doc_type"},
    "fee_settings": {"model": FeeSettings, "label": "currency", "where": None, "status": None},
    "payroll_settings": {"model": PayrollSchoolSettings, "label": "pt_state", "where": None, "status": None},
    "hr_settings": {"model": HrSettings, "label": "letter_reference_prefix", "where": None, "status": None},
}


def _guard_actor(actor: CurrentUser, user: User | None) -> None:
    if user is None:
        return
    if user.id == actor.id:
        raise ForbiddenError("You cannot delete your own account")
    if user.is_platform_admin and (user.platform_role or "") == "SUPER_ADMIN":
        raise ForbiddenError("The super admin account cannot be deleted")


async def delete_record(db: AsyncSession, actor: CurrentUser, kind: str, record_id: UUID, body: RecordDeleteIn) -> None:
    meta = kind_or_404(kind)
    if meta["confirm"] == "name":
        if not (body.confirm_name or "").strip():
            raise ValidationError("Type the name to confirm this deletion")
    elif not body.confirm:
        raise ValidationError("Confirm this deletion")

    if kind == "schools":
        school = await db.get(School, record_id)
        if school is None:
            raise NotFoundError("School not found")
        if not names_match(school.name, body.confirm_name):
            raise ValidationError("Type the school name exactly to confirm deletion")
        await PlatformService(db).delete_school(actor, record_id, body.confirm_name or "")
        return

    if kind == "users":
        await _delete_membership(db, actor, record_id)
        return
    if kind == "platform_staff":
        await _delete_platform_staff(db, actor, record_id)
        return
    await _delete_row(db, actor, kind, record_id)


async def _delete_membership(db: AsyncSession, actor: CurrentUser, membership_id: UUID) -> None:
    mem = await db.get(SchoolMembership, membership_id)
    if mem is None or mem.status != MembershipStatus.ACTIVE.value:
        raise NotFoundError("User not found")
    user = await db.get(User, mem.user_id)
    _guard_actor(actor, user)
    mem.status = MembershipStatus.LEFT.value
    if mem.role == UserRole.STUDENT.value:
        await db.execute(
            update(Student)
            .where(Student.school_id == mem.school_id, Student.user_id == mem.user_id)
            .values(status=PersonStatus.LEFT.value)
        )
        await db.execute(
            update(Enrollment)
            .where(Enrollment.school_id == mem.school_id, Enrollment.status == EnrollmentStatus.ACTIVE.value)
            .where(Enrollment.student_id.in_(select(Student.id).where(Student.user_id == mem.user_id, Student.school_id == mem.school_id)))
            .values(status=EnrollmentStatus.WITHDRAWN.value)
        )
    elif mem.role == UserRole.TEACHER.value:
        await db.execute(
            update(Teacher)
            .where(Teacher.school_id == mem.school_id, Teacher.user_id == mem.user_id)
            .values(status=PersonStatus.LEFT.value)
        )
    elif mem.role == UserRole.PARENT.value:
        await db.execute(
            update(Parent)
            .where(Parent.school_id == mem.school_id, Parent.user_id == mem.user_id)
            .values(status=PersonStatus.LEFT.value)
        )
    else:
        await db.execute(
            update(Employee)
            .where(Employee.school_id == mem.school_id, Employee.user_id == mem.user_id, Employee.status == "ACTIVE")
            .values(status="LEFT", exit_date=date.today())
        )
    remaining = int(
        (
            await db.execute(
                select(func.count())
                .select_from(SchoolMembership)
                .where(
                    SchoolMembership.user_id == mem.user_id,
                    SchoolMembership.status == MembershipStatus.ACTIVE.value,
                    SchoolMembership.id != mem.id,
                )
            )
        ).scalar_one()
    )
    if user is not None and remaining == 0 and not user.is_platform_admin:
        user.is_active = False
        user.status = "INACTIVE"
    label = user.full_name if user else str(mem.user_id)
    await db.flush()
    await _audit(db, actor, kind="users", record_id=mem.id, school_id=mem.school_id, label=f"{label} · {mem.role}", mode="soft")


async def _delete_platform_staff(db: AsyncSession, actor: CurrentUser, staff_id: UUID) -> None:
    staff = await db.get(PlatformStaff, staff_id)
    if staff is None or staff.status != "ACTIVE":
        raise NotFoundError("Staff member not found")
    user = await db.get(User, staff.user_id)
    _guard_actor(actor, user)
    staff.status = "REVOKED"
    await db.flush()
    await _audit(
        db,
        actor,
        kind="platform_staff",
        record_id=staff.id,
        school_id=None,
        label=user.full_name if user else str(staff.user_id),
        mode="soft",
    )


async def _delete_row(db: AsyncSession, actor: CurrentUser, kind: str, record_id: UUID) -> None:
    spec = _LIST[kind]
    model = spec["model"]
    row = await db.get(model, record_id)
    if row is None:
        raise NotFoundError("Record not found")
    label = str(getattr(row, spec["label"], None) or kind)
    school_id = getattr(row, "school_id", None)
    mode = "soft"
    if kind == "students":
        row.status = PersonStatus.LEFT.value
        await db.execute(
            update(Enrollment)
            .where(Enrollment.student_id == row.id, Enrollment.status == EnrollmentStatus.ACTIVE.value)
            .values(status=EnrollmentStatus.WITHDRAWN.value)
        )
    elif kind == "parents":
        row.status = PersonStatus.LEFT.value
    elif kind == "teachers":
        row.status = PersonStatus.LEFT.value
    elif kind == "employees":
        row.status = "LEFT"
        row.exit_date = date.today()
    elif kind == "subjects":
        row.status = SubjectStatus.ARCHIVED.value
    elif kind == "homework":
        row.status = HomeworkStatus.CLOSED.value
    elif kind == "announcements":
        row.status = AnnouncementStatus.ARCHIVED.value
    elif kind == "fee_invoices":
        row.status = FeeInvoiceStatus.CANCELLED.value
        from src.shared.utils import utcnow

        row.cancelled_at = utcnow()
        row.cancel_reason = "Deleted by platform super admin"
    elif kind == "fee_payments":
        row.status = PaymentStatus.CANCELLED.value
    elif kind == "fee_structures":
        row.status = FeeStructureStatus.ARCHIVED.value
        row.active = False
    elif kind == "payroll_runs":
        row.status = "CANCELLED"
        await db.execute(
            update(PayrollPayslip)
            .where(PayrollPayslip.payroll_run_id == row.id, PayrollPayslip.status != "CANCELLED")
            .values(status="CANCELLED")
        )
    elif kind == "payslips":
        row.status = "CANCELLED"
    elif kind == "expenses":
        # Ledger lines are append-only, so the voucher stays and is taken off the books.
        row.status = "REJECTED"
        row.decision_note = "Deleted by platform super admin"
    elif kind == "documents":
        row.is_active = False
    elif kind in {"classes", "family_documents", "fee_settings", "payroll_settings", "hr_settings"}:
        mode = "hard"
        try:
            async with db.begin_nested():
                await db.delete(row)
                await db.flush()
        except IntegrityError as exc:
            raise ConflictError("This record is still linked to other data and was not deleted") from exc
    else:
        raise NotFoundError("Unknown record type")
    if mode == "soft":
        await db.flush()
    await _audit(db, actor, kind=kind, record_id=record_id, school_id=school_id, label=label, mode=mode)


@router.get("/kinds", response_model=list[RecordKindOut])
async def record_kinds(_: Annotated[CurrentUser, Depends(require_super_admin)]):
    return [RecordKindOut(**item) for item in KINDS]


@router.get("/{kind}", response_model=Page[RecordOut])
async def list_kind(
    kind: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_super_admin)],
    school_id: UUID | None = None,
    q: str | None = Query(default=None, max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    await set_rls_bypass(db, True)
    items, total = await list_records(db, kind, school_id=school_id, q=q, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.delete("/{kind}/{record_id}", status_code=204)
async def delete_kind(
    kind: str,
    record_id: UUID,
    body: RecordDeleteIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_super_admin)],
):
    await set_rls_bypass(db, True)
    await delete_record(db, actor, kind, record_id, body)
