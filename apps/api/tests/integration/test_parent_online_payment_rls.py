"""A parent can start and verify an online payment for their own child under forced RLS,
and cannot start one for a child they are not linked to."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.core.database import AsyncSessionLocal
from src.core.exceptions import ForbiddenError
from src.db.models.fee import FeeReceipt, Payment
from src.db.models.parent import Parent
from src.db.models.student_parent import StudentParent
from src.db.models import Student, User
from src.modules.fees.schemas import PaymentOrderIn, PaymentVerifyIn
from src.modules.fees.payment_service import PaymentService
from src.shared.enums import PaymentStatus

from tests.integration.test_finance_tenant import _invoice, _school, _wipe

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


async def _as_parent(db, school_id, user_id):
    db.info["rls_tenant"] = {
        "school_id": str(school_id),
        "user_id": str(user_id),
        "role": "PARENT",
        "bypass": "off",
    }
    await db.execute(text("SET ROLE sybeez_app"))


async def test_parent_pays_own_child_invoice_under_rls():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school, student_user, student, _year, account = await _school(db, "Parent Pay")
            school_ids.append(school.id)
            user_ids.append(student_user.id)
            parent_user = User(
                id=uuid4(),
                email=f"{uuid4().hex}@parent.test",
                full_name="Test Parent",
                status="ACTIVE",
                is_active=True,
                is_platform_admin=False,
            )
            other_user = User(
                id=uuid4(),
                email=f"{uuid4().hex}@other.test",
                full_name="Other Child",
                status="ACTIVE",
                is_active=True,
                is_platform_admin=False,
            )
            db.add_all([parent_user, other_user])
            user_ids += [parent_user.id, other_user.id]
            await db.flush()
            parent = Parent(id=uuid4(), school_id=school.id, user_id=parent_user.id)
            other_student = Student(id=uuid4(), school_id=school.id, user_id=other_user.id, status="ACTIVE")
            db.add_all([parent, other_student])
            await db.flush()
            db.add(StudentParent(id=uuid4(), school_id=school.id, student_id=student.id, parent_id=parent.id))
            from src.db.models.fee import StudentFeeAccount

            other_acc = StudentFeeAccount(
                id=uuid4(),
                school_id=school.id,
                student_id=other_student.id,
                academic_year_id=_year.id,
                status="ACTIVE",
            )
            db.add(other_acc)
            await db.flush()
            mine = _invoice(school.id, account.id, "INV-P-1")
            theirs = _invoice(school.id, other_acc.id, "INV-P-2")
            db.add_all([mine, theirs])
            await db.commit()
            mine_id, theirs_id, sid, puid = mine.id, theirs.id, school.id, parent_user.id

        async with AsyncSessionLocal() as db:
            await _as_parent(db, sid, puid)
            svc = PaymentService(db)
            with pytest.raises(Exception) as denied:
                await svc.create_order(sid, puid, "PARENT", PaymentOrderIn(invoice_id=theirs_id))
            assert isinstance(denied.value, ForbiddenError) or "not found" in str(denied.value).lower()
            await db.rollback()

        async with AsyncSessionLocal() as db:
            await _as_parent(db, sid, puid)
            svc = PaymentService(db)
            order = await svc.create_order(
                sid, puid, "PARENT", PaymentOrderIn(invoice_id=mine_id, idempotency_key="pp-1")
            )
            assert order.order_id
            again = await svc.create_order(
                sid, puid, "PARENT", PaymentOrderIn(invoice_id=mine_id, idempotency_key="pp-1")
            )
            assert again.payment_id == order.payment_id

        async with AsyncSessionLocal() as db:
            await _as_parent(db, sid, puid)
            out = await PaymentService(db).verify_payment(
                sid,
                puid,
                PaymentVerifyIn(
                    payment_id=order.payment_id,
                    gateway_order_id=order.order_id,
                    gateway_payment_id=f"mock_pay_{uuid4().hex}",
                    signature="sig",
                ),
            )
            status = out["status"] if isinstance(out, dict) else out.status
            assert status == PaymentStatus.SUCCESS.value
            await db.execute(text("RESET ROLE"))
            await db.commit()

        async with AsyncSessionLocal() as db:
            pay = await db.get(Payment, order.payment_id)
            assert pay.status == PaymentStatus.SUCCESS.value
            receipts = (await db.execute(select(FeeReceipt).where(FeeReceipt.payment_id == pay.id))).scalars().all()
            assert len(receipts) == 1
    finally:
        await _wipe(school_ids, user_ids)
