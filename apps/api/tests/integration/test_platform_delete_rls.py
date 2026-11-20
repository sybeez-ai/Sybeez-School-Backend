"""Platform Super Admin deletes as sybeez_app with forced RLS and no school on the token.

The session starts the way a live platform request does: role PLATFORM_ADMIN,
empty school, bypass off. Delete school, remove admin, and delete plan must
turn bypass on and pin the school before they write.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.api.dependencies import CurrentUser
from src.core.database import AsyncSessionLocal
from src.db.models import PlatformPlan, School, SchoolMembership, User
from src.modules.platform.schemas import PlanCreate, SchoolAdminCreate, SchoolCreate
from src.modules.platform.saas_service import PlatformSaasService
from src.modules.platform.service import PlatformService
from src.shared.enums import UserRole

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


@pytest.fixture(autouse=True)
def _no_cognito(monkeypatch):
    from src.modules.auth import cognito

    monkeypatch.setattr(cognito, "cognito_configured", lambda: False)


def _actor(user_id) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        email=f"super-{user_id.hex[:8]}@rls-example.com",
        full_name="Platform Super Admin",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
    )


async def _as_platform(db, actor: CurrentUser) -> None:
    db.info["rls_tenant"] = {
        "school_id": "",
        "user_id": str(actor.id),
        "role": "PLATFORM_ADMIN",
        "bypass": "off",
    }
    await db.execute(text("SET LOCAL ROLE sybeez_app"))


async def _wipe(school_ids: list, user_ids: list, plan_ids: list) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET session_replication_role = replica"))
        for pid in plan_ids:
            await db.execute(text("DELETE FROM platform_plans WHERE id = :id"), {"id": pid})
        for sid in school_ids:
            await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": sid})
        for uid in user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
        await db.execute(text("SET session_replication_role = origin"))
        await db.commit()


async def test_super_admin_removes_an_admin_deletes_the_school_and_deletes_a_plan_under_rls():
    actor_id = uuid4()
    school_ids: list = []
    user_ids: list = [actor_id]
    plan_ids: list = []
    actor = _actor(actor_id)
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                User(
                    id=actor_id,
                    email=actor.email,
                    full_name=actor.full_name,
                    is_active=True,
                    is_platform_admin=True,
                    platform_role="SUPER_ADMIN",
                )
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            school = await PlatformService(db).create_school(
                actor,
                SchoolCreate(
                    name=f"RLS Delete {uuid4().hex[:6]}",
                    admin=SchoolAdminCreate(
                        full_name="Temp Admin",
                        email=f"{uuid4().hex}@rls-example.com",
                        password="TempPass123!",
                    ),
                ),
            )
            school_ids.append(school.id)
            user_ids.append(school.admin_user_id)
            plan = await PlatformSaasService(db).create_plan(
                actor,
                PlanCreate(code=f"rls{uuid4().hex[:8]}", name="RLS Plan"),
            )
            plan_ids.append(plan.id)
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            from datetime import datetime, timedelta, timezone

            from src.db.models import RefreshToken

            db.add(
                RefreshToken(
                    user_id=school.admin_user_id,
                    jti=uuid4().hex,
                    token_hash="hash",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    family_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                    revoked=False,
                )
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            await PlatformService(db).remove_school_admin(actor, school.id, school.admin_user_id)
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            from src.modules.fees.tenant import open_platform_school

            await open_platform_school(db, school.id)
            membership = (
                await db.execute(
                    select(SchoolMembership.status).where(
                        SchoolMembership.school_id == school.id,
                        SchoolMembership.user_id == school.admin_user_id,
                    )
                )
            ).scalar_one()
            assert membership == "LEFT"
            listed, _total = await PlatformService(db).list_school_admins(school_id=school.id)
            assert all(item.id != school.admin_user_id for item in listed)
            revoked = (
                await db.execute(
                    text(
                        "SELECT bool_and(revoked) FROM refresh_tokens WHERE user_id = :uid"
                    ),
                    {"uid": school.admin_user_id},
                )
            ).scalar_one()
            assert revoked is True

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            await PlatformSaasService(db).delete_plan(actor, plan.id)
            plan_ids.remove(plan.id)
            assert await db.get(PlatformPlan, plan.id) is None
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            await PlatformService(db).delete_school(actor, school.id, school.name)
            school_ids.remove(school.id)
            assert await db.get(School, school.id) is None
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids, plan_ids)


async def test_purge_deletes_students_fees_attendance_and_ai_usage_under_rls():
    """Append-only fee rows and child records must go with the school."""
    from datetime import date, datetime, timezone
    from decimal import Decimal

    from src.db.models import (
        AcademicYear,
        Attendance,
        ClassRoom,
        FeeAuditLog,
        FeeLedgerEntry,
        Grade,
        PlatformAiUsageDaily,
        PlatformAiUserUsage,
        Student,
    )
    from src.modules.platform.purge import ForeignKey, missing_from_purge, purge_steps

    actor_id = uuid4()
    school_ids: list = []
    user_ids: list = [actor_id]
    actor = _actor(actor_id)
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                User(
                    id=actor_id,
                    email=actor.email,
                    full_name=actor.full_name,
                    is_active=True,
                    is_platform_admin=True,
                    platform_role="SUPER_ADMIN",
                )
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            school = await PlatformService(db).create_school(
                actor,
                SchoolCreate(
                    name=f"RLS Purge {uuid4().hex[:6]}",
                    admin=SchoolAdminCreate(
                        full_name="Purge Admin",
                        email=f"{uuid4().hex}@rls-example.com",
                        password="TempPass123!",
                    ),
                ),
            )
            school_ids.append(school.id)
            user_ids.append(school.admin_user_id)
            pupil_id = uuid4()
            user_ids.append(pupil_id)
            db.add(User(id=pupil_id, email=f"{pupil_id.hex[:8]}@rls-example.com", full_name="Purge Pupil", is_active=True))
            await db.flush()
            student = Student(school_id=school.id, user_id=pupil_id, admission_no="P1", status="ACTIVE")
            db.add(student)
            await db.flush()
            year = AcademicYear(
                school_id=school.id,
                name="2026-27",
                starts_on=date(2026, 6, 1),
                ends_on=date(2027, 3, 31),
                is_current=True,
            )
            db.add(year)
            grade = Grade(school_id=school.id, name="Grade 1", level=1)
            db.add(grade)
            await db.flush()
            classroom = ClassRoom(school_id=school.id, grade_id=grade.id, academic_year_id=year.id, name="A")
            db.add(classroom)
            await db.flush()
            db.add(
                Attendance(
                    school_id=school.id,
                    student_id=student.id,
                    class_id=classroom.id,
                    attended_on=date(2026, 11, 15),
                    status="PRESENT",
                )
            )
            db.add(
                FeeLedgerEntry(
                    school_id=school.id,
                    student_id=student.id,
                    entry_date=date(2026, 11, 15),
                    entry_type="CHARGE",
                    debit=Decimal("10.00"),
                    credit=Decimal("0"),
                    ref_type="invoice",
                    narration="purge",
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                FeeAuditLog(
                    school_id=school.id,
                    actor_user_id=actor.id,
                    action="charge",
                    entity_type="invoice",
                )
            )
            db.add(
                PlatformAiUsageDaily(
                    usage_date=datetime.now(timezone.utc),
                    school_id=school.id,
                    provider="qwen",
                    model="qwen3.8-flash",
                    capability="CHAT",
                    requests=1,
                    input_tokens=3,
                    output_tokens=4,
                )
            )
            db.add(
                PlatformAiUserUsage(school_id=school.id, user_id=pupil_id, model="qwen3.8-flash", tokens=7)
            )
            await db.commit()
            school_id = school.id
            school_name = school.name

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            catalog_tables = {
                row[0]
                for row in (
                    await db.execute(
                        text(
                            """
                            SELECT c.table_name
                            FROM information_schema.columns c
                            JOIN information_schema.tables t
                              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                            WHERE c.table_schema = 'public' AND c.column_name = 'school_id'
                              AND t.table_type = 'BASE TABLE'
                            """
                        )
                    )
                ).all()
            }
            keys = [
                ForeignKey(str(child), str(parent), tuple(child_cols), tuple(parent_cols))
                for child, parent, child_cols, parent_cols in (
                    await db.execute(
                        text(
                            """
                            SELECT child.relname, parent.relname,
                                   array_agg(child_att.attname ORDER BY u.ord),
                                   array_agg(parent_att.attname ORDER BY u.ord)
                            FROM pg_constraint fk
                            JOIN pg_class child ON child.oid = fk.conrelid
                            JOIN pg_class parent ON parent.oid = fk.confrelid
                            JOIN pg_namespace ns ON ns.oid = child.relnamespace
                            JOIN LATERAL unnest(fk.conkey, fk.confkey)
                              WITH ORDINALITY AS u(child_num, parent_num, ord) ON true
                            JOIN pg_attribute child_att
                              ON child_att.attrelid = child.oid AND child_att.attnum = u.child_num
                            JOIN pg_attribute parent_att
                              ON parent_att.attrelid = parent.oid AND parent_att.attnum = u.parent_num
                            WHERE fk.contype = 'f' AND ns.nspname = 'public'
                            GROUP BY fk.oid, child.relname, parent.relname
                            """
                        )
                    )
                ).all()
            ]
            plan = {step.table for step in purge_steps(catalog_tables, keys)}
            assert missing_from_purge(catalog_tables, keys, plan) == []
            for required in ("students", "fee_ledger_entries", "fee_audit_logs", "attendance", "platform_ai_usage_daily"):
                assert required in plan
            await PlatformService(db).delete_school(actor, school_id, school_name)
            school_ids.remove(school_id)
            await db.commit()

        async with AsyncSessionLocal() as db:
            for table in (
                "students",
                "fee_ledger_entries",
                "fee_audit_logs",
                "attendance",
                "platform_ai_usage_daily",
                "platform_ai_user_usage",
            ):
                left = (
                    await db.execute(
                        text(f'SELECT count(*) FROM "{table}" WHERE school_id = :sid'),
                        {"sid": school_id},
                    )
                ).scalar_one()
                assert int(left or 0) == 0, table
            assert await db.get(School, school_id) is None
    finally:
        await _wipe(school_ids, user_ids, [])
