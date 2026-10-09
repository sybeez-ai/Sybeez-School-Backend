"""Seed Sybeez platform + Green Valley demo school users for local development.

Usage: python -m src.db.seed

Local password accounts (no Cognito sub) so email/password login always works in
auth_mode=local|both. Cognito-linked super admin is also upserted when present.
"""

from __future__ import annotations

import asyncio
import os

from datetime import date, datetime, time

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
    TeacherAssignment,
    TimetableEntry,
    User,
)
from src.shared.enums import (
    EnrollmentStatus,
    MembershipStatus,
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

# Primary Dev Login Super Admin (local password — keep in sync with frontend DEV_LOGIN_ACCOUNTS)
PLATFORM_SUPER_ADMIN_EMAIL = "admin@sybeez.ai"
PLATFORM_SUPER_ADMIN_NAME = "Sybeez Super Admin"
PLATFORM_SUPER_ADMIN_PASSWORD = "Silver@1999"

PLATFORM_DEV_EMAIL = "platform.dev@sybeez.ai"
PLATFORM_DEV_NAME = "Sybeez Platform Dev"

SCHOOL_SLUG = "green-valley"
SCHOOL_ADMIN_EMAIL = "admin@greenvalley.edu"
SCHOOL_ADMIN_PASSWORD = DEV_PASSWORD  # DevLogin123! — same as other portal Dev logins
TEACHER_EMAIL = "teacher@greenvalley.edu"
STUDENT_EMAIL = "student@greenvalley.edu"
DEMO_STUDENT_EMAIL = "demo@sybeez.ai"
DEMO_STUDENT_PASSWORD = "Silver@1999"
PARENT_EMAIL = "parent@greenvalley.edu"

APP_ENV = (os.getenv("APP_ENV") or "development").lower().strip()


def _assert_safe_to_seed() -> None:
    # Seeding creates known-password accounts intended for local development only.
    if APP_ENV == "production":
        raise RuntimeError("Refusing to seed in APP_ENV=production. Use migrations + real provisioning.")


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
    _assert_safe_to_seed()
    await ensure_schema()

    async with AsyncSessionLocal() as db:
        await upsert_super_admin(db)

        # Primary Dev Login Super Admin (admin@sybeez.ai)
        platform_super = await upsert_local_user(
            db,
            email=PLATFORM_SUPER_ADMIN_EMAIL,
            full_name=PLATFORM_SUPER_ADMIN_NAME,
            password=PLATFORM_SUPER_ADMIN_PASSWORD,
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

        await db.commit()
        print("Seed complete.")
        print(f"  Cognito super admin : {SUPER_ADMIN_EMAIL}")
        print(f"  School              : Green Valley School ({SCHOOL_SLUG})")
        print(f"  School id           : {school.id}")
        if APP_ENV == "development":
            print(f"  Platform Super Admin: {PLATFORM_SUPER_ADMIN_EMAIL} / {PLATFORM_SUPER_ADMIN_PASSWORD}")
            print(f"  Platform (dev)      : {PLATFORM_DEV_EMAIL} / {DEV_PASSWORD}")
            print(f"  School admin        : {SCHOOL_ADMIN_EMAIL} / {SCHOOL_ADMIN_PASSWORD}")
            print(f"  Teacher             : {TEACHER_EMAIL} / {DEV_PASSWORD}")
            print(f"  Student             : {STUDENT_EMAIL} / {DEV_PASSWORD}")
            print(f"  Student (AI demo)   : {DEMO_STUDENT_EMAIL} / {DEMO_STUDENT_PASSWORD}")
            print(f"  Parent              : {PARENT_EMAIL} / {DEV_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed())
