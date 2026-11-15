"""Super Admin AI token totals. Counts only — no prices."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models import (
    ClassRoom,
    Enrollment,
    Grade,
    PlatformAiUsageDaily,
    PlatformAiUserUsage,
    School,
    SchoolMembership,
    Student,
    Teacher,
    User,
)
from src.shared.enums import EnrollmentStatus, MembershipStatus, PersonStatus, UserRole


def school_token_rows(pairs: list[tuple]) -> list[dict]:
    """Collapse daily rows into one token total per school, split by model. Drops money."""
    totals: dict[UUID | None, dict] = {}
    for item in pairs:
        if len(item) == 4:
            school_id, name, model, tokens = item
        else:
            school_id, name, tokens = item
            model = "unknown"
        row = totals.setdefault(
            school_id,
            {"school_id": school_id, "school_name": name or "Platform", "tokens": 0, "models": []},
        )
        count = int(tokens or 0)
        row["tokens"] += count
        row["models"].append({"model": model or "unknown", "tokens": count})
        if name:
            row["school_name"] = name
    for row in totals.values():
        row["models"].sort(key=lambda entry: (-entry["tokens"], entry["model"]))
    return sorted(totals.values(), key=lambda item: (-item["tokens"], item["school_name"]))


async def list_school_tokens(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(
                PlatformAiUsageDaily.school_id,
                School.name,
                PlatformAiUsageDaily.model,
                func.sum(PlatformAiUsageDaily.input_tokens + PlatformAiUsageDaily.output_tokens),
            )
            .outerjoin(School, School.id == PlatformAiUsageDaily.school_id)
            .group_by(PlatformAiUsageDaily.school_id, School.name, PlatformAiUsageDaily.model)
        )
    ).all()
    return school_token_rows(
        [(school_id, name, model, int(tokens or 0)) for school_id, name, model, tokens in rows]
    )


async def school_people_tokens(db: AsyncSession, school_id: UUID) -> dict:
    school = await db.get(School, school_id)
    if school is None:
        raise NotFoundError("School not found")
    school_tokens = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(PlatformAiUsageDaily.input_tokens + PlatformAiUsageDaily.output_tokens), 0)).where(
                    PlatformAiUsageDaily.school_id == school_id
                )
            )
        ).scalar_one()
    )
    usage = dict(
        (
            await db.execute(
                select(PlatformAiUserUsage.user_id, PlatformAiUserUsage.tokens).where(PlatformAiUserUsage.school_id == school_id)
            )
        ).all()
    )

    students = []
    student_rows = (
        await db.execute(
            select(Student, User, ClassRoom.name, Grade.name)
            .join(User, User.id == Student.user_id)
            .outerjoin(
                Enrollment,
                (Enrollment.student_id == Student.id)
                & (Enrollment.school_id == school_id)
                & (Enrollment.status == EnrollmentStatus.ACTIVE.value),
            )
            .outerjoin(ClassRoom, ClassRoom.id == Enrollment.class_id)
            .outerjoin(Grade, Grade.id == ClassRoom.grade_id)
            .where(Student.school_id == school_id, Student.status == PersonStatus.ACTIVE.value)
            .order_by(User.full_name)
        )
    ).all()
    seen: set[UUID] = set()
    for student, user, class_name, grade_name in student_rows:
        if user.id in seen:
            continue
        seen.add(user.id)
        standard = " ".join(part for part in (grade_name, class_name) if part) or None
        students.append(
            {
                "user_id": user.id,
                "student_id": student.id,
                "name": user.full_name,
                "role": UserRole.STUDENT.value,
                "standard": standard,
                "admission_no": student.admission_no,
                "email": user.email,
                "tokens": int(usage.get(user.id, 0) or 0),
            }
        )

    teachers = []
    teacher_rows = (
        await db.execute(
            select(Teacher, User)
            .join(User, User.id == Teacher.user_id)
            .where(Teacher.school_id == school_id, Teacher.status == PersonStatus.ACTIVE.value)
            .order_by(User.full_name)
        )
    ).all()
    for _teacher, user in teacher_rows:
        seen.add(user.id)
        teachers.append(
            {
                "user_id": user.id,
                "teacher_id": _teacher.id,
                "name": user.full_name,
                "role": UserRole.TEACHER.value,
                "email": user.email,
                "tokens": int(usage.get(user.id, 0) or 0),
            }
        )

    others = []
    member_rows = (
        await db.execute(
            select(SchoolMembership, User)
            .join(User, User.id == SchoolMembership.user_id)
            .where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
                SchoolMembership.role.notin_([UserRole.STUDENT.value, UserRole.TEACHER.value]),
            )
            .order_by(User.full_name, SchoolMembership.role)
        )
    ).all()
    for membership, user in member_rows:
        if user.id in seen and membership.role in {UserRole.STUDENT.value, UserRole.TEACHER.value}:
            continue
        others.append(
            {
                "user_id": user.id,
                "membership_id": membership.id,
                "name": user.full_name,
                "role": membership.role,
                "email": user.email,
                "tokens": int(usage.get(user.id, 0) or 0),
            }
        )

    assigned = sum(int(value or 0) for value in usage.values())
    model_rows = (
        await db.execute(
            select(
                PlatformAiUsageDaily.model,
                func.sum(PlatformAiUsageDaily.input_tokens + PlatformAiUsageDaily.output_tokens),
            )
            .where(PlatformAiUsageDaily.school_id == school_id)
            .group_by(PlatformAiUsageDaily.model)
        )
    ).all()

    def with_models(person: dict) -> dict:
        count = int(person.get("tokens") or 0)
        person["models"] = [{"model": "recorded", "tokens": count}] if count else []
        return person

    return {
        "school_id": school.id,
        "school_name": school.name,
        "tokens": school_tokens,
        "models": [
            {"model": model or "unknown", "tokens": int(tokens or 0)}
            for model, tokens in sorted(model_rows, key=lambda row: -int(row[1] or 0))
        ],
        "unassigned_tokens": max(0, school_tokens - assigned),
        "students": [with_models(person) for person in students],
        "teachers": [with_models(person) for person in teachers],
        "others": [with_models(person) for person in others],
    }
