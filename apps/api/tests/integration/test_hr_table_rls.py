"""Forced row-level security on the HR tables added after 0050.

sybeez_app cannot bypass RLS. A session sees only its school's leave types,
holidays, letter templates, and employment history. Teacher and staff can read
the leave calendar for the session school and cannot read letters or history,
and they cannot write leave types.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from src.core.database import AsyncSessionLocal
from src.db.models.hr import Employee, EmployeeHistory, HrHoliday, HrLeaveType, HrLetterTemplate
from src.db.models.school import School
from src.db.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


async def _wipe(school_ids: list, user_ids: list) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("RESET ROLE"))
        await db.execute(text("SET session_replication_role = replica"))
        for school_id in school_ids:
            await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": school_id})
        for user_id in user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await db.execute(text("SET session_replication_role = origin"))
        await db.execute(text("SELECT set_config('app.school_id', '', false)"))
        await db.execute(text("SELECT set_config('app.user_id', '', false)"))
        await db.execute(text("SELECT set_config('app.role', '', false)"))
        await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
        await db.commit()


def _school(name: str) -> School:
    slug = f"hr-rls-{uuid4().hex[:10]}"
    return School(id=uuid4(), name=name, slug=slug, status="ACTIVE", timezone="Asia/Kolkata", currency="INR")


def _user(label: str) -> User:
    return User(
        id=uuid4(),
        email=f"{label}-{uuid4().hex[:8]}@example.com",
        full_name=label,
        is_active=True,
        is_platform_admin=False,
    )


async def _as(db, *, user_id, school_id, role: str) -> None:
    await db.execute(text("SET ROLE sybeez_app"))
    await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
    await db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": str(user_id)})
    await db.execute(text("SELECT set_config('app.school_id', :v, false)"), {"v": str(school_id)})
    await db.execute(text("SELECT set_config('app.role', :v, false)"), {"v": role})


async def test_new_hr_tables_are_forced_and_isolated():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school_a = _school("HR RLS A")
            school_b = _school("HR RLS B")
            user_a = _user("admin-a")
            user_b = _user("admin-b")
            staff_a = _user("staff-a")
            db.add_all([school_a, school_b, user_a, user_b, staff_a])
            await db.flush()
            employee = Employee(
                id=uuid4(),
                school_id=school_a.id,
                staff_type="NON_TEACHING",
                full_name="Ada Staff",
                status="ACTIVE",
                user_id=staff_a.id,
            )
            db.add(employee)
            await db.flush()
            db.add_all(
                [
                    HrLeaveType(
                        id=uuid4(), school_id=school_a.id, code="CL", name="Casual", accrual="ANNUAL", created_by=user_a.id
                    ),
                    HrLeaveType(
                        id=uuid4(), school_id=school_b.id, code="CL", name="Casual", accrual="ANNUAL", created_by=user_b.id
                    ),
                    HrHoliday(
                        id=uuid4(),
                        school_id=school_a.id,
                        holiday_date=date(2026, 10, 2),
                        name="Gandhi Jayanti",
                        created_by=user_a.id,
                    ),
                    HrHoliday(
                        id=uuid4(),
                        school_id=school_b.id,
                        holiday_date=date(2026, 10, 2),
                        name="Gandhi Jayanti",
                        created_by=user_b.id,
                    ),
                    HrLetterTemplate(
                        id=uuid4(),
                        school_id=school_a.id,
                        letter_type="OFFER",
                        version=1,
                        title="Offer",
                        body="Welcome",
                        created_by=user_a.id,
                    ),
                    HrLetterTemplate(
                        id=uuid4(),
                        school_id=school_b.id,
                        letter_type="OFFER",
                        version=1,
                        title="Offer",
                        body="Welcome",
                        created_by=user_b.id,
                    ),
                    EmployeeHistory(
                        id=uuid4(),
                        school_id=school_a.id,
                        employee_id=employee.id,
                        event_type="JOINED",
                        effective_date=date(2026, 4, 1),
                        actor_user_id=user_a.id,
                    ),
                ]
            )
            await db.commit()
            school_ids += [school_a.id, school_b.id]
            user_ids += [user_a.id, user_b.id, staff_a.id]
            staff_id = staff_a.id
            school_a_id = school_a.id
            school_b_id = school_b.id
            admin_a = user_a.id

        async with AsyncSessionLocal() as db:
            forced = (
                await db.execute(
                    text(
                        """
                        SELECT relname
                        FROM pg_class
                        WHERE relname IN (
                            'hr_leave_types', 'hr_holidays', 'hr_letter_templates', 'employee_history'
                        )
                          AND relrowsecurity AND relforcerowsecurity
                        """
                    )
                )
            ).scalars().all()
            assert set(forced) == {
                "hr_leave_types",
                "hr_holidays",
                "hr_letter_templates",
                "employee_history",
            }

            await _as(db, user_id=admin_a, school_id=school_a_id, role="HR")
            visible = (await db.execute(select(HrLeaveType.school_id))).scalars().all()
            assert set(visible) == {school_a_id}
            holidays = (await db.execute(select(HrHoliday.school_id))).scalars().all()
            assert set(holidays) == {school_a_id}
            letters = (await db.execute(select(HrLetterTemplate.school_id))).scalars().all()
            assert set(letters) == {school_a_id}
            history = (await db.execute(select(EmployeeHistory.school_id))).scalars().all()
            assert set(history) == {school_a_id}

            await db.execute(text("SELECT set_config('app.role', 'STAFF', false)"))
            await db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": str(staff_id)})
            assert set((await db.execute(select(HrLeaveType.school_id))).scalars().all()) == {school_a_id}
            assert (await db.execute(select(HrLetterTemplate.id))).scalars().all() == []
            assert (await db.execute(select(EmployeeHistory.id))).scalars().all() == []

            with pytest.raises(DBAPIError) as exc:
                async with db.begin_nested():
                    db.add(
                        HrLeaveType(
                            id=uuid4(),
                            school_id=school_a_id,
                            code="SL",
                            name="Sick",
                            accrual="ANNUAL",
                        )
                    )
                    await db.flush()
            assert "row-level security" in str(exc.value).lower() or "new row violates" in str(exc.value).lower()

            await db.execute(text("SELECT set_config('app.school_id', :v, false)"), {"v": str(school_b_id)})
            assert set((await db.execute(select(HrLeaveType.school_id))).scalars().all()) == {school_b_id}
            assert set((await db.execute(select(HrHoliday.school_id))).scalars().all()) == {school_b_id}
            assert (await db.execute(select(HrLetterTemplate.id))).scalars().all() == []
            assert (await db.execute(select(EmployeeHistory.id))).scalars().all() == []

            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids)
