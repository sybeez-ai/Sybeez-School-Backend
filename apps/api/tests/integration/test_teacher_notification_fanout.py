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


async def test_teacher_publishes_draft_and_student_is_notified():
    """Draft -> publish by the assigned teacher under RLS (was a 500 / 403 live)."""
    from datetime import date

    from sqlalchemy import select

    from src.db.models import AcademicYear, ClassRoom, Enrollment, Grade, Student, Subject, Teacher, TeacherAssignment
    from src.modules.homework.schemas import HomeworkCreate
    from src.modules.homework.service import HomeworkService
    from src.shared.enums import HomeworkStatus

    school_id, teacher_uid, student_uid = await _seed()
    try:
        async with AsyncSessionLocal() as db:
            year = AcademicYear(id=uuid4(), school_id=school_id, name="2026-27", starts_on=date(2026, 4, 1),
                                ends_on=date(2027, 3, 31), is_current=True)
            grade = Grade(id=uuid4(), school_id=school_id, name="Class 5", level=5)
            db.add_all([year, grade])
            await db.flush()
            room = ClassRoom(id=uuid4(), school_id=school_id, grade_id=grade.id, academic_year_id=year.id,
                             name="5-A", section="A")
            subject = Subject(id=uuid4(), school_id=school_id, name="Maths", code=f"M{uuid4().hex[:6]}")
            teacher = Teacher(id=uuid4(), school_id=school_id, user_id=teacher_uid, status="ACTIVE")
            student = Student(id=uuid4(), school_id=school_id, user_id=student_uid, status="ACTIVE")
            db.add_all([room, subject, teacher, student])
            await db.flush()
            db.add_all([
                TeacherAssignment(id=uuid4(), school_id=school_id, teacher_id=teacher.id, class_id=room.id,
                                  subject_id=subject.id, academic_year_id=year.id),
                Enrollment(id=uuid4(), school_id=school_id, student_id=student.id, class_id=room.id,
                           academic_year_id=year.id, status="ACTIVE"),
            ])
            await db.commit()
            room_id, subject_id = room.id, subject.id

        async with AsyncSessionLocal() as db:
            await _teacher_session(db, school_id, teacher_uid)
            svc = HomeworkService(db)
            draft = await svc.create(
                school_id=school_id, user_id=teacher_uid, role="TEACHER",
                data=HomeworkCreate(class_id=room_id, subject_id=subject_id, title="Test draft",
                                    status=HomeworkStatus.DRAFT),
            )
            await db.commit()
            out = await svc.publish(school_id=school_id, user_id=teacher_uid, role="TEACHER", homework_id=draft.id)
            assert out.status == HomeworkStatus.PUBLISHED.value
            assert out.updated_at is not None
            await db.commit()

        async with AsyncSessionLocal() as db:
            await db.execute(text("RESET ROLE"))
            await db.execute(text("SELECT set_config('app.rls_bypass', 'on', false)"))
            got = (await db.execute(select(Notification).where(Notification.user_id == student_uid))).scalars().all()
            await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
            assert any(n.category == "HOMEWORK_PUBLISHED" for n in got)
    finally:
        await _wipe([school_id], [teacher_uid, student_uid])


async def _seed_class(school_id, teacher_uid, student_uid):
    from datetime import date

    from src.db.models import AcademicYear, ClassRoom, Enrollment, Grade, Student, Subject, Teacher, TeacherAssignment

    async with AsyncSessionLocal() as db:
        year = AcademicYear(id=uuid4(), school_id=school_id, name="2026-27", starts_on=date(2026, 4, 1),
                            ends_on=date(2027, 3, 31), is_current=True)
        grade = Grade(id=uuid4(), school_id=school_id, name="Class 5", level=5)
        db.add_all([year, grade])
        await db.flush()
        room = ClassRoom(id=uuid4(), school_id=school_id, grade_id=grade.id, academic_year_id=year.id,
                         name="5-A", section="A")
        subject = Subject(id=uuid4(), school_id=school_id, name="Maths", code=f"M{uuid4().hex[:6]}")
        teacher = Teacher(id=uuid4(), school_id=school_id, user_id=teacher_uid, status="ACTIVE")
        student = Student(id=uuid4(), school_id=school_id, user_id=student_uid, status="ACTIVE")
        db.add_all([room, subject, teacher, student])
        await db.flush()
        db.add_all([
            TeacherAssignment(id=uuid4(), school_id=school_id, teacher_id=teacher.id, class_id=room.id,
                              subject_id=subject.id, academic_year_id=year.id),
            Enrollment(id=uuid4(), school_id=school_id, student_id=student.id, class_id=room.id,
                       academic_year_id=year.id, status="ACTIVE"),
        ])
        await db.commit()
        return room.id, subject.id, student.id


@pytest.mark.parametrize("status", ["PRESENT", "ABSENT"])
async def test_teacher_marks_attendance_under_rls(status):
    from datetime import date

    from src.modules.attendance.schemas import AttendanceMarkItem, AttendanceMarkRequest
    from src.modules.attendance.service import AttendanceService

    school_id, teacher_uid, student_uid = await _seed()
    try:
        room_id, _subject_id, student_id = await _seed_class(school_id, teacher_uid, student_uid)
        async with AsyncSessionLocal() as db:
            await _teacher_session(db, school_id, teacher_uid)
            out = await AttendanceService(db).mark(
                school_id=school_id, user_id=teacher_uid, role="TEACHER",
                data=AttendanceMarkRequest(class_id=room_id, attended_on=date.today(),
                                           marks=[AttendanceMarkItem(student_id=student_id, status=status)]),
            )
            await db.commit()
            assert out and out[0].status == status
    finally:
        await _wipe([school_id], [teacher_uid, student_uid])


async def test_publish_draft_with_linked_parent():
    from src.db.models import Parent, StudentParent
    from src.modules.homework.schemas import HomeworkCreate
    from src.modules.homework.service import HomeworkService
    from src.shared.enums import HomeworkStatus

    school_id, teacher_uid, student_uid = await _seed()
    parent_user = _user("Test Parent")
    try:
        room_id, subject_id, student_id = await _seed_class(school_id, teacher_uid, student_uid)
        async with AsyncSessionLocal() as db:
            db.add(parent_user)
            await db.flush()
            parent = Parent(id=uuid4(), school_id=school_id, user_id=parent_user.id, status="ACTIVE")
            db.add(parent)
            await db.flush()
            db.add(StudentParent(id=uuid4(), school_id=school_id, student_id=student_id, parent_id=parent.id,
                                 relation_type="mother", is_primary=True))
            await db.commit()
        async with AsyncSessionLocal() as db:
            await _teacher_session(db, school_id, teacher_uid)
            svc = HomeworkService(db)
            d = await svc.create(school_id=school_id, user_id=teacher_uid, role="TEACHER",
                                 data=HomeworkCreate(class_id=room_id, subject_id=subject_id, title="Test d",
                                                     status=HomeworkStatus.DRAFT))
            await db.commit()
            out = await svc.publish(school_id=school_id, user_id=teacher_uid, role="TEACHER", homework_id=d.id)
            await db.commit()
            assert out.status == "PUBLISHED"
    finally:
        await _wipe([school_id], [teacher_uid, student_uid, parent_user.id])
