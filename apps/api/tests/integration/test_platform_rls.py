"""Cross-school and parent/child row-level security as sybeez_app (NOBYPASSRLS)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.core.database import AsyncSessionLocal
from src.db.models import (
    AcademicYear,
    Attendance,
    ClassRoom,
    Enrollment,
    Exam,
    ExamSubject,
    FeeInvoice,
    Grade,
    Homework,
    HomeworkSubmission,
    Parent,
    School,
    Student,
    StudentFeeAccount,
    StudentMark,
    StudentParent,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.shared.enums import FeeInvoiceStatus

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


def _user(name: str) -> User:
    return User(
        id=uuid4(),
        email=f"{uuid4().hex}@rls.test",
        full_name=name,
        status="ACTIVE",
        is_active=True,
        is_platform_admin=False,
    )


def _invoice(school_id, account_id, number: str) -> FeeInvoice:
    amount = Decimal("500.00")
    return FeeInvoice(
        id=uuid4(),
        school_id=school_id,
        student_fee_account_id=account_id,
        invoice_number=number,
        invoice_date=date(2026, 4, 10),
        due_date=date(2026, 4, 20),
        currency="INR",
        subtotal=amount,
        discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        total_amount=amount,
        paid_amount=Decimal("0"),
        balance_amount=amount,
        status=FeeInvoiceStatus.ISSUED.value,
    )


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


async def _as_role(db, *, school_id, user_id, role: str) -> None:
    await db.execute(text("SET ROLE sybeez_app"))
    await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
    await db.execute(text("SELECT set_config('app.school_id', :v, false)"), {"v": str(school_id)})
    await db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": str(user_id)})
    await db.execute(text("SELECT set_config('app.role', :v, false)"), {"v": role})


async def test_parent_sees_only_own_children():
    """A parent sees their linked child, not a classmate or another school."""
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            school = School(
                id=uuid4(),
                name="RLS Parent School",
                slug=f"rls-{uuid4().hex[:16]}",
                status="ACTIVE",
                timezone="Asia/Kolkata",
                currency="INR",
            )
            other = School(
                id=uuid4(),
                name="RLS Other School",
                slug=f"rls-{uuid4().hex[:16]}",
                status="ACTIVE",
                timezone="Asia/Kolkata",
                currency="INR",
            )
            parent_user = _user("Parent")
            child_user = _user("Child")
            sibling_user = _user("Classmate")
            teacher_user = _user("Teacher")
            outsider_user = _user("Outsider")
            db.add_all([school, other, parent_user, child_user, sibling_user, teacher_user, outsider_user])
            await db.flush()
            school_ids += [school.id, other.id]
            user_ids += [parent_user.id, child_user.id, sibling_user.id, teacher_user.id, outsider_user.id]

            year = AcademicYear(
                id=uuid4(),
                school_id=school.id,
                name="2026-27",
                starts_on=date(2026, 4, 1),
                ends_on=date(2027, 3, 31),
                is_current=True,
            )
            grade = Grade(id=uuid4(), school_id=school.id, name="Class 5", level=5)
            db.add_all([year, grade])
            await db.flush()
            classroom = ClassRoom(
                id=uuid4(),
                school_id=school.id,
                grade_id=grade.id,
                academic_year_id=year.id,
                name="5-A",
                section="A",
            )
            subject = Subject(id=uuid4(), school_id=school.id, name="Maths", code=f"M{uuid4().hex[:6]}")
            child = Student(id=uuid4(), school_id=school.id, user_id=child_user.id, status="ACTIVE")
            sibling = Student(id=uuid4(), school_id=school.id, user_id=sibling_user.id, status="ACTIVE")
            outsider = Student(id=uuid4(), school_id=other.id, user_id=outsider_user.id, status="ACTIVE")
            teacher = Teacher(id=uuid4(), school_id=school.id, user_id=teacher_user.id, status="ACTIVE")
            parent = Parent(id=uuid4(), school_id=school.id, user_id=parent_user.id, status="ACTIVE")
            db.add_all([classroom, subject, child, sibling, outsider, teacher, parent])
            await db.flush()
            db.add(
                StudentParent(
                    id=uuid4(),
                    school_id=school.id,
                    student_id=child.id,
                    parent_id=parent.id,
                    relation_type="mother",
                    is_primary=True,
                )
            )
            db.add(
                TeacherAssignment(
                    id=uuid4(),
                    school_id=school.id,
                    academic_year_id=year.id,
                    teacher_id=teacher.id,
                    subject_id=subject.id,
                    class_id=classroom.id,
                )
            )
            for student, roll in ((child, "1"), (sibling, "2")):
                db.add(
                    Enrollment(
                        id=uuid4(),
                        school_id=school.id,
                        student_id=student.id,
                        academic_year_id=year.id,
                        grade_id=grade.id,
                        class_id=classroom.id,
                        roll_number=roll,
                        status="ACTIVE",
                    )
                )
                db.add(
                    Attendance(
                        id=uuid4(),
                        school_id=school.id,
                        student_id=student.id,
                        class_id=classroom.id,
                        attended_on=date(2026, 4, 10),
                        status="PRESENT",
                    )
                )
            child_account = StudentFeeAccount(
                id=uuid4(),
                school_id=school.id,
                student_id=child.id,
                academic_year_id=year.id,
                status="ACTIVE",
            )
            sibling_account = StudentFeeAccount(
                id=uuid4(),
                school_id=school.id,
                student_id=sibling.id,
                academic_year_id=year.id,
                status="ACTIVE",
            )
            db.add_all([child_account, sibling_account])
            await db.flush()
            child_invoice = _invoice(school.id, child_account.id, f"INV-{uuid4().hex[:8]}")
            sibling_invoice = _invoice(school.id, sibling_account.id, f"INV-{uuid4().hex[:8]}")
            homework = Homework(
                id=uuid4(),
                school_id=school.id,
                academic_year_id=year.id,
                class_id=classroom.id,
                subject_id=subject.id,
                teacher_id=teacher.id,
                title="Fractions",
            )
            exam = Exam(id=uuid4(), school_id=school.id, academic_year_id=year.id, title="Term 1")
            db.add_all([child_invoice, sibling_invoice, homework, exam])
            await db.flush()
            exam_subject = ExamSubject(
                id=uuid4(),
                school_id=school.id,
                exam_id=exam.id,
                subject_id=subject.id,
                grade_id=grade.id,
            )
            db.add(exam_subject)
            await db.flush()
            for student in (child, sibling):
                db.add(
                    HomeworkSubmission(
                        id=uuid4(),
                        school_id=school.id,
                        homework_id=homework.id,
                        student_id=student.id,
                        content="answer",
                    )
                )
                db.add(
                    StudentMark(
                        id=uuid4(),
                        school_id=school.id,
                        exam_id=exam.id,
                        exam_subject_id=exam_subject.id,
                        student_id=student.id,
                        class_id=classroom.id,
                        obtained_marks=80,
                        max_marks=100,
                    )
                )
            await db.commit()
            ids = {
                "school": school.id,
                "child": child.id,
                "sibling": sibling.id,
                "parent_user": parent_user.id,
                "child_user": child_user.id,
                "teacher_user": teacher_user.id,
                "child_invoice": child_invoice.id,
                "homework": homework.id,
            }

        async with AsyncSessionLocal() as db:
            await _as_role(db, school_id=ids["school"], user_id=ids["parent_user"], role="PARENT")
            students = set((await db.execute(select(Student.id))).scalars().all())
            assert students == {ids["child"]}
            attendance = set((await db.execute(select(Attendance.student_id))).scalars().all())
            assert attendance == {ids["child"]}
            marks = set((await db.execute(select(StudentMark.student_id))).scalars().all())
            assert marks == {ids["child"]}
            invoices = set((await db.execute(select(FeeInvoice.id))).scalars().all())
            assert invoices == {ids["child_invoice"]}
            submissions = set((await db.execute(select(HomeworkSubmission.student_id))).scalars().all())
            assert submissions == {ids["child"]}
            homework_ids = set((await db.execute(select(Homework.id))).scalars().all())
            assert homework_ids == {ids["homework"]}

            await db.execute(text("SELECT set_config('app.role', '', false)"))
            assert (await db.execute(select(Student.id))).scalars().all() == []

            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_role(db, school_id=ids["school"], user_id=ids["child_user"], role="STUDENT")
            assert set((await db.execute(select(Student.id))).scalars().all()) == {ids["child"]}
            assert set((await db.execute(select(Attendance.student_id))).scalars().all()) == {ids["child"]}
            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_role(db, school_id=ids["school"], user_id=ids["teacher_user"], role="TEACHER")
            assert set((await db.execute(select(Attendance.student_id))).scalars().all()) == {
                ids["child"],
                ids["sibling"],
            }
            assert set((await db.execute(select(StudentMark.student_id))).scalars().all()) == {
                ids["child"],
                ids["sibling"],
            }
            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids)
