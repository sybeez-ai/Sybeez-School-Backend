"""Owners read a payslip through RLS only after it is published or paid."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.core.database import AsyncSessionLocal
from src.db.models import Employee, PayrollPayslip, PayrollRun
from src.db.models.school import School
from src.db.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


async def _wipe(school_ids, user_ids) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("RESET ROLE"))
        await db.execute(text("SET session_replication_role = replica"))
        for school_id in school_ids:
            await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": school_id})
        for user_id in user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await db.execute(text("SET session_replication_role = origin"))
        await db.commit()


async def test_staff_rls_hides_draft_payslips():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school = School(
                id=uuid4(),
                name="Payslip RLS",
                slug=f"slip-rls-{uuid4().hex[:10]}",
                status="ACTIVE",
                timezone="Asia/Kolkata",
                currency="INR",
            )
            owner = User(
                id=uuid4(),
                email=f"owner-{uuid4().hex[:8]}@example.test",
                full_name="Owner",
                is_active=True,
                is_platform_admin=False,
            )
            other = User(
                id=uuid4(),
                email=f"other-{uuid4().hex[:8]}@example.test",
                full_name="Other",
                is_active=True,
                is_platform_admin=False,
            )
            db.add_all([school, owner, other])
            await db.flush()
            employee = Employee(
                id=uuid4(),
                school_id=school.id,
                staff_type="NON_TEACHING",
                full_name="Owner",
                status="ACTIVE",
                user_id=owner.id,
            )
            stranger = Employee(
                id=uuid4(),
                school_id=school.id,
                staff_type="NON_TEACHING",
                full_name="Stranger",
                status="ACTIVE",
                user_id=other.id,
            )
            db.add_all([employee, stranger])
            await db.flush()
            draft_run = PayrollRun(
                id=uuid4(),
                school_id=school.id,
                label="Draft",
                period_start=date(2026, 8, 1),
                period_end=date(2026, 8, 31),
                status="DRAFT",
            )
            live_run = PayrollRun(
                id=uuid4(),
                school_id=school.id,
                label="Live",
                period_start=date(2026, 9, 1),
                period_end=date(2026, 9, 30),
                status="DRAFT",
                approval_status="PUBLISHED",
            )
            db.add_all([draft_run, live_run])
            await db.flush()
            draft = PayrollPayslip(
                id=uuid4(),
                school_id=school.id,
                payroll_run_id=draft_run.id,
                employee_id=employee.id,
                gross_amount=Decimal("100.00"),
                deductions_amount=Decimal("0.00"),
                net_amount=Decimal("100.00"),
                status="DRAFT",
            )
            published = PayrollPayslip(
                id=uuid4(),
                school_id=school.id,
                payroll_run_id=live_run.id,
                employee_id=employee.id,
                gross_amount=Decimal("200.00"),
                deductions_amount=Decimal("0.00"),
                net_amount=Decimal("200.00"),
                status="PUBLISHED",
            )
            theirs = PayrollPayslip(
                id=uuid4(),
                school_id=school.id,
                payroll_run_id=live_run.id,
                employee_id=stranger.id,
                gross_amount=Decimal("50.00"),
                deductions_amount=Decimal("0.00"),
                net_amount=Decimal("50.00"),
                status="PUBLISHED",
            )
            db.add_all([draft, published, theirs])
            await db.commit()
            school_ids.append(school.id)
            user_ids.extend([owner.id, other.id])
            owner_id = owner.id
            school_id = school.id
            published_id = published.id

        async with AsyncSessionLocal() as db:
            await db.execute(text("SET ROLE sybeez_app"))
            await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
            await db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": str(owner_id)})
            await db.execute(text("SELECT set_config('app.school_id', :v, false)"), {"v": str(school_id)})
            await db.execute(text("SELECT set_config('app.role', 'STAFF', false)"))
            seen = set((await db.execute(select(PayrollPayslip.id))).scalars().all())
            assert seen == {published_id}
            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids)
