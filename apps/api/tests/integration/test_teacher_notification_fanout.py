"""A teacher publishing homework / marking attendance notifies students and parents.

notifications are user-private under RLS; before the fan-out scope a TEACHER session could not
insert a student's row and the whole request failed with 403 "outside your school ... scope".
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.core.database import AsyncSessionLocal
from src.db.models import Notification, School
from src.modules.notifications.service import create_in_app_notification
from tests.integration.test_platform_rls import _as_role, _user, _wipe

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


async def _seed():
    async with AsyncSessionLocal() as db:
        school = School(
            id=uuid4(), name="Test Fanout", slug=f"fan-{uuid4().hex[:12]}", status="ACTIVE",
            timezone="Asia/Kolkata", currency="INR",
        )
        teacher, student = _user("Test Teacher"), _user("Test Student")
        db.add_all([school, teacher, student])
        await db.commit()
        return school.id, teacher.id, student.id


async def _teacher_session(db, school_id, teacher_id):
    db.info["rls_tenant"] = {
        "school_id": str(school_id), "user_id": str(teacher_id), "role": "TEACHER", "bypass": "off",
    }
    await _as_role(db, school_id=school_id, user_id=teacher_id, role="TEACHER")


async def test_teacher_can_notify_student_in_own_school():
    school_id, teacher_id, student_id = await _seed()
    try:
        async with AsyncSessionLocal() as db:
            await _teacher_session(db, school_id, teacher_id)
            row = await create_in_app_notification(
                db, school_id=school_id, user_id=student_id, title="New homework", body="Test",
                category="HOMEWORK_PUBLISHED", event_key=f"t:{uuid4()}",
            )
            assert row is not None
            # bypass is switched back off after the fan-out
            assert (await db.execute(text("SELECT current_setting('app.rls_bypass', true)"))).scalar() == "off"
            await db.commit()
    finally:
        await _wipe([school_id], [teacher_id, student_id])


async def test_raw_insert_for_other_user_still_blocked():
    school_id, teacher_id, student_id = await _seed()
    try:
        async with AsyncSessionLocal() as db:
            await _teacher_session(db, school_id, teacher_id)
            db.add(Notification(school_id=school_id, user_id=student_id, title="x", body="x", channel="IN_APP"))
            with pytest.raises(Exception, match="row-level security"):
                await db.flush()
            await db.rollback()
    finally:
        await _wipe([school_id], [teacher_id, student_id])


async def test_scope_does_not_cross_schools():
    school_id, teacher_id, student_id = await _seed()
    other_id, t2, s2 = await _seed()
    try:
        async with AsyncSessionLocal() as db:
            await _teacher_session(db, school_id, teacher_id)
            with pytest.raises(Exception, match="row-level security"):
                await create_in_app_notification(
                    db, school_id=other_id, user_id=s2, title="x", body="x", category="HOMEWORK_PUBLISHED",
                )
            await db.rollback()
    finally:
        await _wipe([school_id, other_id], [teacher_id, student_id, t2, s2])
