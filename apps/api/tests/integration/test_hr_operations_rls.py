"""Forced row-level security on the HR operations tables."""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.core.database import AsyncSessionLocal
from src.db.models.hr_operations import HrJobOpening, HrProbation
from src.db.models.school import School
from src.db.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


def _school(name: str) -> School:
    return School(
        id=uuid4(),
        name=name,
        slug=f"ops-rls-{uuid4().hex[:10]}",
        status="ACTIVE",
        timezone="Asia/Kolkata",
        currency="INR",
    )


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


async def test_probation_and_openings_are_school_scoped():
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school_a = _school("Ops A")
            school_b = _school("Ops B")
            user_a = User(id=uuid4(), email=f"a-{uuid4().hex[:8]}@example.com", full_name="A", is_active=True, is_platform_admin=False)
            user_b = User(id=uuid4(), email=f"b-{uuid4().hex[:8]}@example.com", full_name="B", is_active=True, is_platform_admin=False)
            db.add_all([school_a, school_b, user_a, user_b])
            await db.flush()
            from src.db.models import Employee

            employee = Employee(
                id=uuid4(),
                school_id=school_a.id,
                staff_type="NON_TEACHING",
                full_name="Ada",
                status="ACTIVE",
                user_id=user_a.id,
            )
            db.add(employee)
            await db.flush()
            db.add_all(
                [
                    HrProbation(
                        id=uuid4(),
                        school_id=school_a.id,
                        employee_id=employee.id,
                        subject_user_id=user_a.id,
                        start_date=date(2026, 4, 1),
                        end_date=date(2026, 9, 30),
                        status="ACTIVE",
                    ),
                    HrJobOpening(id=uuid4(), school_id=school_a.id, title="Teacher", status="OPEN"),
                    HrJobOpening(id=uuid4(), school_id=school_b.id, title="Teacher", status="OPEN"),
                ]
            )
            await db.commit()
            school_ids += [school_a.id, school_b.id]
            user_ids += [user_a.id, user_b.id]
            school_a_id = school_a.id
            user_a_id = user_a.id

        async with AsyncSessionLocal() as db:
            await db.execute(text("SET ROLE sybeez_app"))
            await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
            await db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": str(user_a_id)})
            await db.execute(text("SELECT set_config('app.school_id', :v, false)"), {"v": str(school_a_id)})
            await db.execute(text("SELECT set_config('app.role', 'HR', false)"))
            assert set((await db.execute(select(HrProbation.school_id))).scalars().all()) == {school_a_id}
            assert set((await db.execute(select(HrJobOpening.school_id))).scalars().all()) == {school_a_id}

            await db.execute(text("SELECT set_config('app.role', 'STAFF', false)"))
            assert set((await db.execute(select(HrProbation.school_id))).scalars().all()) == {school_a_id}
            assert (await db.execute(select(HrJobOpening.id))).scalars().all() == []
            await db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": str(user_ids[1])})
            assert (await db.execute(select(HrProbation.id))).scalars().all() == []
            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids)
