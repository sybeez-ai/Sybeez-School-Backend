"""Seed Sybeez platform + Green Valley demo school users for local development.

Usage: python -m src.db.seed

Local password accounts (no Cognito sub) so email/password login always works in
auth_mode=local|both. Cognito-linked super admin is also upserted when present.
"""

from __future__ import annotations

import asyncio
import os
import sys

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select, text

from src.core.database import AsyncSessionLocal, engine
from src.core.security import hash_password
from src.db.base import Base
from src.db import models  # noqa: F401 — register all models
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Grade,
    Parent,
    School,
    SchoolMembership,
    Student,
    StudentParent,
    Subject,
    Subscription,
    Teacher,
    Announcement,
    AnnouncementAudience,
    Attendance,
    Exam,
    ExamSection,
    ExamSubject,
    ExamType,
    MessageBroadcast,
    NotificationDelivery,
    OnlineTest,
    OnlineTestQuestion,
    OnlineTestQuestionKey,
    QuestionBankItem,
    StudentMark,
    TeacherAssignment,
    TimetableEntry,
    User,
)
from src.db.models.class_subject import ClassSubject
from src.shared.enums import (
    AnnouncementStatus,
    AttendanceStatus,
    AudienceType,
    EnrollmentStatus,
    ExamAttendanceStatus,
    ExamStatus,
    MarkEntryStatus,
    MembershipStatus,
    NotificationPriority,
    PlatformRole,
    SchoolStatus,
    SubscriptionStatus,
    UserRole,
)
from src.shared.utils import utcnow

# Cognito-backed super admin (optional; password lives in Cognito)
SUPER_ADMIN_EMAIL = "silverstalan.inbaraj@sybeez.ai"
SUPER_ADMIN_NAME = "Silverstalan Inbaraj"
SUPER_ADMIN_COGNITO_SUB = "245844c8-d0e1-7029-ecfe-acbcc636dae9"

# Local-password demo accounts — used by frontend Dev login + manual credential login
DEV_PASSWORD = "DevLogin123!"

# Primary Dev Login Super Admin. The password is DEMO_PLATFORM_PASSWORD, never a literal.
PLATFORM_SUPER_ADMIN_EMAIL = "admin@sybeez.ai"
PLATFORM_SUPER_ADMIN_NAME = "Sybeez Super Admin"

PLATFORM_DEV_EMAIL = "platform.dev@sybeez.ai"
PLATFORM_DEV_NAME = "Sybeez Platform Dev"

SCHOOL_SLUG = "green-valley"
SCHOOL_ADMIN_EMAIL = "admin@greenvalley.edu"
SCHOOL_ADMIN_PASSWORD = DEV_PASSWORD  # DevLogin123! — same as other portal Dev logins
TEACHER_EMAIL = "teacher@greenvalley.edu"
STUDENT_EMAIL = "student@greenvalley.edu"
DEMO_STUDENT_EMAIL = "demo@sybeez.ai"
DEMO_STUDENT_PASSWORD = DEV_PASSWORD
PARENT_EMAIL = "parent@greenvalley.edu"

APP_ENV = (os.getenv("APP_ENV") or "development").lower().strip()


def _assert_safe_to_seed() -> None:
    # Seeding creates known-password accounts intended for local development only.
    if APP_ENV == "production":
        raise RuntimeError("Refusing to seed in APP_ENV=production. Use migrations + real provisioning.")


def _enabled() -> bool:
    return os.environ.get("SEED_DEMO_DATA", "").strip().lower() in {"1", "true", "yes"}



async def seed_school_admin_demo(db, *, school, year, grade, classroom, subject, teacher, school_admin) -> None:
    """Extra local-only demo data for the school-admin portal (insights, tests, notices)."""
    from sqlalchemy import select

    # Extra school-wide subjects
    extra_subjects = [("English", "ENG"), ("Science", "SCI"), ("Social Science", "SOC")]
    subjects = {subject.code: subject}
    for name, code in extra_subjects:
        row = (
            await db.execute(select(Subject).where(Subject.school_id == school.id, Subject.code == code))
        ).scalar_one_or_none()
        if row is None:
            row = Subject(school_id=school.id, name=name, code=code)
            db.add(row)
            await db.flush()
        subjects[code] = row

    # Class subjects: Maths + English with teacher, Science without (optional teacher)
    desired = [
        (subjects["MATH"], teacher.id),
        (subjects["ENG"], teacher.id),
        (subjects["SCI"], None),
        (subjects["SOC"], None),
    ]
    for subj, teacher_id in desired:
        existing = (
            await db.execute(
                select(ClassSubject).where(
                    ClassSubject.class_id == classroom.id,
                    ClassSubject.subject_id == subj.id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                ClassSubject(
                    school_id=school.id,
                    class_id=classroom.id,
                    subject_id=subj.id,
                    teacher_id=teacher_id,
                )
            )

    # A weekday grid so the class timetable is not just "today"
    weekly = [
        (0, time(9, 0), time(9, 45), subjects["MATH"], "Room 12"),
        (0, time(10, 0), time(10, 45), subjects["ENG"], "Room 12"),
        (1, time(9, 0), time(9, 45), subjects["SCI"], "Lab 2"),
        (1, time(10, 0), time(10, 45), subjects["SOC"], "Room 12"),
        (2, time(9, 0), time(9, 45), subjects["ENG"], "Room 12"),
        (2, time(11, 0), time(11, 45), subjects["MATH"], "Room 12"),
        (3, time(9, 0), time(9, 45), subjects["SCI"], "Lab 2"),
        (4, time(9, 0), time(9, 45), subjects["MATH"], "Room 12"),
        (4, time(10, 0), time(10, 45), subjects["ENG"], "Room 12"),
    ]
    for dow, starts_at, ends_at, subj, room in weekly:
        existing_tt = (
            await db.execute(
                select(TimetableEntry).where(
                    TimetableEntry.school_id == school.id,
                    TimetableEntry.class_id == classroom.id,
                    TimetableEntry.day_of_week == dow,
                    TimetableEntry.starts_at == starts_at,
                )
            )
        ).scalar_one_or_none()
        if existing_tt is None:
            db.add(
                TimetableEntry(
                    school_id=school.id,
                    academic_year_id=year.id,
                    class_id=classroom.id,
                    subject_id=subj.id,
                    teacher_id=teacher.id if subj.code in {"MATH", "ENG"} else None,
                    day_of_week=dow,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    room=room,
                )
            )

    enrollments = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.school_id == school.id,
                Enrollment.class_id == classroom.id,
                Enrollment.status == EnrollmentStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    student_ids = [e.student_id for e in enrollments]
    today = date.today()
    # Last 5 weekdays of attendance (mix of present / late / absent)
    days: list[date] = []
    cursor = today
    while len(days) < 5:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    pattern = [
        AttendanceStatus.PRESENT.value,
        AttendanceStatus.PRESENT.value,
        AttendanceStatus.LATE.value,
        AttendanceStatus.PRESENT.value,
        AttendanceStatus.ABSENT.value,
    ]
    marked_at = datetime.now(timezone.utc)
    for offset, day in enumerate(days):
        for i, sid in enumerate(student_ids):
            existing = (
                await db.execute(
                    select(Attendance).where(
                        Attendance.student_id == sid,
                        Attendance.class_id == classroom.id,
                        Attendance.attended_on == day,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                status = pattern[(offset + i) % len(pattern)]
                db.add(
                    Attendance(
                        school_id=school.id,
                        student_id=sid,
                        class_id=classroom.id,
                        attended_on=day,
                        status=status,
                        marked_by=school_admin.id if school_admin else None,
                        marked_at=marked_at,
                    )
                )

    # Written exam + marks (for Tests tab and Insights)
    exam_type = (
        await db.execute(select(ExamType).where(ExamType.school_id == school.id, ExamType.name == "Term 1"))
    ).scalar_one_or_none()
    if exam_type is None:
        exam_type = ExamType(school_id=school.id, name="Term 1", description=None, is_active=True)
        db.add(exam_type)
        await db.flush()
    exam = (
        await db.execute(select(Exam).where(Exam.school_id == school.id, Exam.title == "Term 1 Mid-term"))
    ).scalar_one_or_none()
    if exam is None:
        exam = Exam(
            school_id=school.id,
            academic_year_id=year.id,
            exam_type_id=exam_type.id,
            grade_id=grade.id,
            class_id=classroom.id,
            subject_id=subject.id,
            title="Term 1 Mid-term",
            starts_on=days[-1],
            ends_on=days[0],
            max_score=100.0,
            status=ExamStatus.PUBLISHED.value,
            published_at=marked_at,
            published_by=school_admin.id if school_admin else None,
            created_by=school_admin.id if school_admin else None,
        )
        db.add(exam)
        await db.flush()
    section = (
        await db.execute(select(ExamSection).where(ExamSection.exam_id == exam.id, ExamSection.class_id == classroom.id))
    ).scalar_one_or_none()
    if section is None:
        section = ExamSection(school_id=school.id, exam_id=exam.id, class_id=classroom.id)
        db.add(section)
        await db.flush()
    scores = {"MATH": [78.0, 91.0, 64.0, 85.0], "ENG": [72.0, 88.0, 69.0, 80.0]}
    for code, marks in scores.items():
        subj = subjects[code]
        exam_subject = (
            await db.execute(
                select(ExamSubject).where(ExamSubject.exam_id == exam.id, ExamSubject.subject_id == subj.id)
            )
        ).scalar_one_or_none()
        if exam_subject is None:
            exam_subject = ExamSubject(
                school_id=school.id,
                exam_id=exam.id,
                subject_id=subj.id,
                grade_id=grade.id,
                max_marks=100.0,
                pass_marks=35.0,
                exam_date=days[0],
            )
            db.add(exam_subject)
            await db.flush()
        for sid, obtained in zip(student_ids, marks):
            existing_mark = (
                await db.execute(
                    select(StudentMark).where(
                        StudentMark.exam_subject_id == exam_subject.id,
                        StudentMark.student_id == sid,
                    )
                )
            ).scalar_one_or_none()
            if existing_mark is None:
                db.add(
                    StudentMark(
                        school_id=school.id,
                        exam_id=exam.id,
                        exam_subject_id=exam_subject.id,
                        student_id=sid,
                        class_id=classroom.id,
                        obtained_marks=obtained,
                        max_marks=100.0,
                        attendance=ExamAttendanceStatus.PRESENT.value,
                        status=MarkEntryStatus.SUBMITTED.value,
                        entered_by=school_admin.id if school_admin else None,
                        entered_at=marked_at,
                    )
                )

    # One published online test
    online = (
        await db.execute(
            select(OnlineTest).where(OnlineTest.school_id == school.id, OnlineTest.title == "Fractions practice")
        )
    ).scalar_one_or_none()
    if online is None:
        online = OnlineTest(
            school_id=school.id,
            teacher_id=teacher.id,
            created_by=school_admin.id if school_admin else None,
            class_id=classroom.id,
            subject_id=subject.id,
            title="Fractions practice",
            instructions="Choose the correct answer.",
            status="PUBLISHED",
            duration_minutes=20,
            total_points=1,
            published_at=marked_at,
            approval_status="APPROVED",
        )
        db.add(online)
        await db.flush()
        question = OnlineTestQuestion(
            school_id=school.id,
            test_id=online.id,
            class_id=classroom.id,
            prompt="What is 1/2 + 1/4?",
            options=[{"id": "a", "text": "1/4"}, {"id": "b", "text": "3/4"}, {"id": "c", "text": "2/4"}, {"id": "d", "text": "1"}],
            points=1,
            sort_order=1,
            source="MANUAL",
        )
        db.add(question)
        await db.flush()
        db.add(
            OnlineTestQuestionKey(
                question_id=question.id,
                school_id=school.id,
                test_id=online.id,
                class_id=classroom.id,
                correct_option_id="b",
                explanation="1/2 + 1/4 = 3/4",
            )
        )

    # One published school notice
    notice = (
        await db.execute(
            select(Announcement).where(
                Announcement.school_id == school.id,
                Announcement.title == "Parent-teacher meeting next Friday",
            )
        )
    ).scalar_one_or_none()
    if notice is None:
        notice = Announcement(
            school_id=school.id,
            author_id=school_admin.id if school_admin else None,
            title="Parent-teacher meeting next Friday",
            body="Please come to the school hall from 4pm to 6pm to meet your child's teachers.",
            is_published=True,
            status=AnnouncementStatus.PUBLISHED.value,
            priority=NotificationPriority.NORMAL.value,
            published_at=marked_at,
        )
        db.add(notice)
        await db.flush()
        db.add(
            AnnouncementAudience(
                school_id=school.id,
                announcement_id=notice.id,
                audience_type=AudienceType.SCHOOL.value,
            )
        )
        notice.channels = ["IN_APP", "WHATSAPP", "EMAIL", "SMS"]

    # Question bank items for the class subject
    existing_bank = (
        await db.execute(
            select(QuestionBankItem).where(
                QuestionBankItem.school_id == school.id,
                QuestionBankItem.prompt == "What is 3/4 as a decimal?",
            )
        )
    ).scalar_one_or_none()
    if existing_bank is None:
        db.add(
            QuestionBankItem(
                school_id=school.id,
                subject_id=subject.id,
                class_id=classroom.id,
                created_by=teacher.user_id if teacher else (school_admin.id if school_admin else None),
                teacher_id=teacher.id if teacher else None,
                prompt="What is 3/4 as a decimal?",
                question_type="mcq",
                options=["0.25", "0.5", "0.75", "1"],
                answer_key="2",
                points=1,
            )
        )
        db.add(
            QuestionBankItem(
                school_id=school.id,
                subject_id=subject.id,
                class_id=classroom.id,
                created_by=teacher.user_id if teacher else (school_admin.id if school_admin else None),
                teacher_id=teacher.id if teacher else None,
                prompt="Explain how to add two fractions with the same denominator.",
                question_type="short",
                options=None,
                answer_key="Add the numerators and keep the denominator.",
                points=2,
            )
        )

    # A teacher-submitted online test waiting on admin approval
    pending = (
        await db.execute(
            select(OnlineTest).where(OnlineTest.school_id == school.id, OnlineTest.title == "Decimals check-in")
        )
    ).scalar_one_or_none()
    if pending is None:
        pending = OnlineTest(
            school_id=school.id,
            teacher_id=teacher.id,
            created_by=teacher.user_id if teacher else None,
            class_id=classroom.id,
            subject_id=subject.id,
            title="Decimals check-in",
            instructions="A short check before the written test.",
            status="DRAFT",
            approval_status="PENDING_APPROVAL",
            submitted_at=marked_at,
            submitted_by=teacher.user_id if teacher else None,
            duration_minutes=15,
            total_points=1,
        )
        db.add(pending)

    # Broadcast history with honest unsent channels (local providers are not live)
    broadcast = (
        await db.execute(
            select(MessageBroadcast).where(
                MessageBroadcast.school_id == school.id,
                MessageBroadcast.body == "Sports day is this Saturday at 9am on the main ground.",
            )
        )
    ).scalar_one_or_none()
    if broadcast is None and school_admin is not None:
        broadcast = MessageBroadcast(
            school_id=school.id,
            created_by=school_admin.id,
            title="Sports day reminder",
            body="Sports day is this Saturday at 9am on the main ground.",
            audience_type="class",
            audience_json={"class_ids": [str(classroom.id)], "include_parents": True, "include_teachers": False},
            channels=["WHATSAPP", "EMAIL", "SMS"],
            recipient_count=1,
        )
        db.add(broadcast)
        await db.flush()
        for channel, dest in (("WHATSAPP", "n/a"), ("EMAIL", "n/a"), ("SMS", "n/a")):
            db.add(
                NotificationDelivery(
                    school_id=school.id,
                    user_id=school_admin.id,
                    channel=channel,
                    notification_type="MESSAGE_BROADCAST",
                    to=dest,
                    title="Sports day reminder",
                    body=broadcast.body,
                    meta_json=f'{{"entity_type": "message_broadcast", "entity_id": "{broadcast.id}"}}',
                    provider="none",
                    status="FAILED",
                    attempts=0,
                    last_error="not sent: provider not configured",
                    error_code="provider_not_configured",
                )
            )


async def ensure_schema() -> None:
    """Create full Core + AI schema (PostgreSQL + pgvector) if missing."""
    if APP_ENV != "development":
        raise RuntimeError(
            "Refusing to create schema outside development. "
            "Use Alembic migrations (make migrate) instead of Base.metadata.create_all."
        )
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS cognito_sub VARCHAR(128)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_role VARCHAR(32)"))
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_cognito_sub ON users (cognito_sub)")
        )


async def upsert_super_admin(db) -> User:
    result = await db.execute(select(User).where(User.email == SUPER_ADMIN_EMAIL.lower()))
    user = result.scalar_one_or_none()
    if user:
        user.full_name = SUPER_ADMIN_NAME
        user.is_active = True
        user.is_platform_admin = True
        user.platform_role = PlatformRole.SUPER_ADMIN.value
        user.cognito_sub = SUPER_ADMIN_COGNITO_SUB
        user.password_hash = None
        return user
    user = User(
        email=SUPER_ADMIN_EMAIL.lower(),
        full_name=SUPER_ADMIN_NAME,
        password_hash=None,
        cognito_sub=SUPER_ADMIN_COGNITO_SUB,
        is_platform_admin=True,
        platform_role=PlatformRole.SUPER_ADMIN.value,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def upsert_local_user(
    db,
    *,
    email: str,
    full_name: str,
    password: str,
    is_platform_admin: bool = False,
    platform_role: str | None = None,
) -> User:
    """Local password user — cognito_sub cleared so password login always works."""
    email = email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        user.full_name = full_name
        user.password_hash = hash_password(password)
        user.cognito_sub = None
        user.is_active = True
        user.is_platform_admin = is_platform_admin
        if is_platform_admin:
            user.platform_role = platform_role or PlatformRole.SUPER_ADMIN.value
        return user
    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        cognito_sub=None,
        is_platform_admin=is_platform_admin,
        platform_role=platform_role if is_platform_admin else None,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def ensure_membership(db, *, user_id, school_id, role: UserRole) -> SchoolMembership:
    membership = (
        await db.execute(
            select(SchoolMembership).where(
                SchoolMembership.user_id == user_id,
                SchoolMembership.school_id == school_id,
                SchoolMembership.role == role.value,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        membership = SchoolMembership(
            user_id=user_id,
            school_id=school_id,
            role=role.value,
            status=MembershipStatus.ACTIVE.value,
        )
        db.add(membership)
        await db.flush()
    else:
        membership.status = MembershipStatus.ACTIVE.value
    return membership


async def seed() -> None:
    global SCHOOL_ADMIN_PASSWORD
    if not _enabled():
        raise SystemExit("Refusing to seed without SEED_DEMO_DATA=true")
    platform_password = os.environ.get("DEMO_PLATFORM_PASSWORD", "").strip()
    school_password = os.environ.get("DEMO_SCHOOL_ADMIN_PASSWORD", "").strip()
    if not platform_password:
        raise SystemExit("DEMO_PLATFORM_PASSWORD is required")
    if not school_password:
        raise SystemExit("DEMO_SCHOOL_ADMIN_PASSWORD is required")
    SCHOOL_ADMIN_PASSWORD = school_password
    _assert_safe_to_seed()
    await ensure_schema()

    async with AsyncSessionLocal() as db:
        await upsert_super_admin(db)

        # Primary Dev Login Super Admin (admin@sybeez.ai)
        platform_super = await upsert_local_user(
            db,
            email=PLATFORM_SUPER_ADMIN_EMAIL,
            full_name=PLATFORM_SUPER_ADMIN_NAME,
            password=platform_password,
            is_platform_admin=True,
            platform_role=PlatformRole.SUPER_ADMIN.value,
        )

        platform_dev = await upsert_local_user(
            db,
            email=PLATFORM_DEV_EMAIL,
            full_name=PLATFORM_DEV_NAME,
            password=DEV_PASSWORD,
            is_platform_admin=True,
            platform_role=PlatformRole.SUPER_ADMIN.value,
        )

        legacy = await db.execute(select(User).where(User.email == "admin@sybeez.com"))
        legacy_user = legacy.scalar_one_or_none()
        if legacy_user and legacy_user.id not in {platform_super.id, platform_dev.id}:
            legacy_user.is_platform_admin = True
            if not legacy_user.platform_role:
                legacy_user.platform_role = PlatformRole.USER.value

        result = await db.execute(select(School).where(School.slug == SCHOOL_SLUG))
        school = result.scalar_one_or_none()
        if school is None:
            school = School(
                name="Green Valley School",
                slug=SCHOOL_SLUG,
                code="GREENVAL",
                status=SchoolStatus.ACTIVE.value,
                country="India",
                city="Chennai",
                timezone="Asia/Kolkata",
            )
            db.add(school)
            await db.flush()
            db.add(
                Subscription(
                    school_id=school.id,
                    plan_code="standard",
                    status=SubscriptionStatus.TRIAL.value,
                    starts_at=utcnow(),
                )
            )
            await db.flush()

        school_admin = await upsert_local_user(
            db,
            email=SCHOOL_ADMIN_EMAIL,
            full_name="Priya Nair",
            password=SCHOOL_ADMIN_PASSWORD,
        )
        await ensure_membership(
            db, user_id=school_admin.id, school_id=school.id, role=UserRole.SCHOOL_ADMIN
        )

        teacher_user = await upsert_local_user(
            db,
            email=TEACHER_EMAIL,
            full_name="Ms. Priya Teacher",
            password=DEV_PASSWORD,
        )
        await ensure_membership(
            db, user_id=teacher_user.id, school_id=school.id, role=UserRole.TEACHER
        )
        teacher = (
            await db.execute(
                select(Teacher).where(
                    Teacher.school_id == school.id, Teacher.user_id == teacher_user.id
                )
            )
        ).scalar_one_or_none()
        if teacher is None:
            teacher = Teacher(
                school_id=school.id,
                user_id=teacher_user.id,
                employee_code="T-001",
                title="Class Teacher",
            )
            db.add(teacher)
            await db.flush()

        student_user = await upsert_local_user(
            db,
            email=STUDENT_EMAIL,
            full_name="Arjun Kumar",
            password=DEV_PASSWORD,
        )
        await ensure_membership(
            db, user_id=student_user.id, school_id=school.id, role=UserRole.STUDENT
        )
        student = (
            await db.execute(
                select(Student).where(
                    Student.school_id == school.id, Student.user_id == student_user.id
                )
            )
        ).scalar_one_or_none()
        if student is None:
            student = Student(
                school_id=school.id,
                user_id=student_user.id,
                admission_no="GV-2401",
            )
            db.add(student)
            await db.flush()

        # Demo Grade 8-A + subject + enrollment + today's timetable for the teacher
        year = (
            await db.execute(
                select(AcademicYear).where(
                    AcademicYear.school_id == school.id, AcademicYear.is_current.is_(True)
                )
            )
        ).scalar_one_or_none()
        if year is None:
            today = date.today()
            year = AcademicYear(
                school_id=school.id,
                name=f"{today.year}-{today.year + 1}",
                starts_on=date(today.year, 4, 1),
                ends_on=date(today.year + 1, 3, 31),
                is_current=True,
            )
            db.add(year)
            await db.flush()

        grade = (
            await db.execute(select(Grade).where(Grade.school_id == school.id, Grade.name == "Grade 8"))
        ).scalar_one_or_none()
        if grade is None:
            grade = Grade(school_id=school.id, name="Grade 8", level=8)
            db.add(grade)
            await db.flush()

        classroom = (
            await db.execute(
                select(ClassRoom).where(
                    ClassRoom.school_id == school.id,
                    ClassRoom.name == "Grade 8-A",
                    ClassRoom.academic_year_id == year.id,
                )
            )
        ).scalar_one_or_none()
        if classroom is None:
            classroom = ClassRoom(
                school_id=school.id,
                grade_id=grade.id,
                academic_year_id=year.id,
                name="Grade 8-A",
                section="A",
                capacity=40,
            )
            db.add(classroom)
            await db.flush()

        subject = (
            await db.execute(
                select(Subject).where(Subject.school_id == school.id, Subject.code == "MATH")
            )
        ).scalar_one_or_none()
        if subject is None:
            subject = Subject(school_id=school.id, name="Mathematics", code="MATH")
            db.add(subject)
            await db.flush()

        assignment = (
            await db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == classroom.id,
                    TeacherAssignment.subject_id == subject.id,
                )
            )
        ).scalar_one_or_none()
        if assignment is None:
            db.add(
                TeacherAssignment(
                    school_id=school.id,
                    academic_year_id=year.id,
                    teacher_id=teacher.id,
                    subject_id=subject.id,
                    class_id=classroom.id,
                )
            )

        enrollment = (
            await db.execute(
                select(Enrollment).where(
                    Enrollment.student_id == student.id,
                    Enrollment.academic_year_id == year.id,
                )
            )
        ).scalar_one_or_none()
        if enrollment is None:
            db.add(
                Enrollment(
                    school_id=school.id,
                    student_id=student.id,
                    academic_year_id=year.id,
                    grade_id=grade.id,
                    class_id=classroom.id,
                    status=EnrollmentStatus.ACTIVE.value,
                )
            )
        else:
            enrollment.class_id = classroom.id
            enrollment.status = EnrollmentStatus.ACTIVE.value

        # AI Teacher demo student (separate demo login)
        demo_user = await upsert_local_user(
            db,
            email=DEMO_STUDENT_EMAIL,
            full_name="Demo Student",
            password=DEMO_STUDENT_PASSWORD,
        )
        await ensure_membership(
            db, user_id=demo_user.id, school_id=school.id, role=UserRole.STUDENT
        )
        demo_student = (
            await db.execute(
                select(Student).where(
                    Student.school_id == school.id, Student.user_id == demo_user.id
                )
            )
        ).scalar_one_or_none()
        if demo_student is None:
            demo_student = Student(
                school_id=school.id,
                user_id=demo_user.id,
                admission_no="GV-DEMO",
            )
            db.add(demo_student)
            await db.flush()
        demo_en = (
            await db.execute(
                select(Enrollment).where(
                    Enrollment.student_id == demo_student.id,
                    Enrollment.academic_year_id == year.id,
                )
            )
        ).scalar_one_or_none()
        if demo_en is None:
            db.add(
                Enrollment(
                    school_id=school.id,
                    student_id=demo_student.id,
                    academic_year_id=year.id,
                    grade_id=grade.id,
                    class_id=classroom.id,
                    status=EnrollmentStatus.ACTIVE.value,
                )
            )
        else:
            demo_en.class_id = classroom.id
            demo_en.status = EnrollmentStatus.ACTIVE.value

        # Extra classmates so attendance roster isn't empty
        for email, name, adm in [
            ("diya@greenvalley.edu", "Diya Patel", "GV-2402"),
            ("rahul@greenvalley.edu", "Rahul Mehta", "GV-2403"),
        ]:
            u = await upsert_local_user(db, email=email, full_name=name, password=DEV_PASSWORD)
            await ensure_membership(db, user_id=u.id, school_id=school.id, role=UserRole.STUDENT)
            st = (
                await db.execute(
                    select(Student).where(Student.school_id == school.id, Student.user_id == u.id)
                )
            ).scalar_one_or_none()
            if st is None:
                st = Student(school_id=school.id, user_id=u.id, admission_no=adm)
                db.add(st)
                await db.flush()
            en = (
                await db.execute(
                    select(Enrollment).where(
                        Enrollment.student_id == st.id, Enrollment.academic_year_id == year.id
                    )
                )
            ).scalar_one_or_none()
            if en is None:
                db.add(
                    Enrollment(
                        school_id=school.id,
                        student_id=st.id,
                        academic_year_id=year.id,
                        grade_id=grade.id,
                        class_id=classroom.id,
                        status=EnrollmentStatus.ACTIVE.value,
                    )
                )

        # Today: one period covering "now", plus morning/afternoon slots
        today = date.today()
        dow = today.weekday()
        now = datetime.now().time()
        # Build a "now" window around current time (±30 min)
        now_minutes = now.hour * 60 + now.minute
        start_m = max(0, now_minutes - 15)
        end_m = min(23 * 60 + 59, now_minutes + 45)
        slots = [
            (time(9, 0), time(9, 45), "Room 12"),
            (time(start_m // 60, start_m % 60), time(end_m // 60, end_m % 60), "Room 12"),
            (time(14, 0), time(14, 45), "Lab 2"),
        ]
        # Deduplicate if current window overlaps 9:00
        unique_slots = []
        seen = set()
        for s, e, room in slots:
            key = (s.hour, s.minute, e.hour, e.minute)
            if key in seen:
                continue
            seen.add(key)
            unique_slots.append((s, e, room))

        for starts_at, ends_at, room in unique_slots:
            existing_tt = (
                await db.execute(
                    select(TimetableEntry).where(
                        TimetableEntry.school_id == school.id,
                        TimetableEntry.teacher_id == teacher.id,
                        TimetableEntry.class_id == classroom.id,
                        TimetableEntry.day_of_week == dow,
                        TimetableEntry.starts_at == starts_at,
                    )
                )
            ).scalar_one_or_none()
            if existing_tt is None:
                db.add(
                    TimetableEntry(
                        school_id=school.id,
                        academic_year_id=year.id,
                        class_id=classroom.id,
                        subject_id=subject.id,
                        teacher_id=teacher.id,
                        day_of_week=dow,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        room=room,
                    )
                )

        parent_user = await upsert_local_user(
            db,
            email=PARENT_EMAIL,
            full_name="Ravi Sharma",
            password=DEV_PASSWORD,
        )
        await ensure_membership(
            db, user_id=parent_user.id, school_id=school.id, role=UserRole.PARENT
        )
        parent = (
            await db.execute(
                select(Parent).where(Parent.school_id == school.id, Parent.user_id == parent_user.id)
            )
        ).scalar_one_or_none()
        if parent is None:
            parent = Parent(school_id=school.id, user_id=parent_user.id, phone="+91-9000000001")
            db.add(parent)
            await db.flush()

        link = (
            await db.execute(
                select(StudentParent).where(
                    StudentParent.student_id == student.id,
                    StudentParent.parent_id == parent.id,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            db.add(
                StudentParent(
                    school_id=school.id,
                    student_id=student.id,
                    parent_id=parent.id,
                    relation_type="father",
                    is_primary=True,
                )
            )

        await seed_school_admin_demo(
            db,
            school=school,
            year=year,
            grade=grade,
            classroom=classroom,
            subject=subject,
            teacher=teacher,
            school_admin=school_admin,
        )

        await db.commit()
        print("Seed complete.")
        print(f"  Cognito super admin : {SUPER_ADMIN_EMAIL}")
        print(f"  School              : Green Valley School ({SCHOOL_SLUG})")
        print(f"  School id           : {school.id}")
        if APP_ENV == "development":
            print(f"  Platform Super Admin: {PLATFORM_SUPER_ADMIN_EMAIL}")
            print(f"  Platform (dev)      : {PLATFORM_DEV_EMAIL} / {DEV_PASSWORD}")
            print(f"  School admin        : {SCHOOL_ADMIN_EMAIL} / {SCHOOL_ADMIN_PASSWORD}")
            print(f"  Teacher             : {TEACHER_EMAIL} / {DEV_PASSWORD}")
            print(f"  Student             : {STUDENT_EMAIL} / {DEV_PASSWORD}")
            print(f"  Student (AI demo)   : {DEMO_STUDENT_EMAIL} / {DEMO_STUDENT_PASSWORD}")
            print(f"  Parent              : {PARENT_EMAIL} / {DEV_PASSWORD}")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise
