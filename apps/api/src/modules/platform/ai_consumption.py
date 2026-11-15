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


def model_breakdown(entries: list[tuple[str | None, int]]) -> list[dict]:
    """Sum tokens for one person by model. An empty list stays empty — no placeholder model."""
    totals: dict[str, int] = {}
    for model, tokens in entries:
        key = (model or "").strip() or "unknown"
        totals[key] = totals.get(key, 0) + int(tokens or 0)
    return [
        {"model": model, "tokens": count}
        for model, count in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]


def school_token_rows(pairs: list[tuple]) -> list[dict]:
    """Collapse daily rows into one token total per school, split by model. Drops money.

    A 4-tuple with model None is a school that has no usage yet: tokens stay 0 and models stays empty.
    """
    totals: dict[UUID | None, dict] = {}
    for item in pairs:
        if len(item) == 4:
            school_id, name, model, tokens = item
        else:
            school_id, name, tokens = item
            model = "unknown"
        row = totals.setdefault(
            school_id,
            {"school_id": school_id, "school_name": name or "Platform", "tokens": 0, "models": {}},
        )
        count = int(tokens or 0)
        row["tokens"] += count
        if model is not None:
            key = (model or "").strip() or "unknown"
            row["models"][key] = row["models"].get(key, 0) + count
        if name:
            row["school_name"] = name
    out = []
    for row in totals.values():
        models = [{"model": model, "tokens": count} for model, count in row["models"].items()]
        models.sort(key=lambda entry: (-entry["tokens"], entry["model"]))
        out.append(
            {
                "school_id": row["school_id"],
                "school_name": row["school_name"],
                "tokens": row["tokens"],
                "models": models,
            }
        )
    return sorted(out, key=lambda item: (-item["tokens"], item["school_name"]))


async def list_school_tokens(db: AsyncSession) -> list[dict]:
    from src.modules.fees.tenant import set_rls_bypass

    await set_rls_bypass(db, True)
    usage = (
        select(
            PlatformAiUsageDaily.school_id.label("school_id"),
            PlatformAiUsageDaily.model.label("model"),
            func.sum(PlatformAiUsageDaily.input_tokens + PlatformAiUsageDaily.output_tokens).label("tokens"),
        )
        .group_by(PlatformAiUsageDaily.school_id, PlatformAiUsageDaily.model)
        .subquery()
    )
    rows = (
        await db.execute(
            select(School.id, School.name, usage.c.model, usage.c.tokens).outerjoin(
                usage, usage.c.school_id == School.id
            )
        )
    ).all()
    daily = school_token_rows(
        [(school_id, name, model, int(tokens or 0)) for school_id, name, model, tokens in rows]
    )
    people = (
        await db.execute(
            select(
                PlatformAiUserUsage.school_id,
                School.name,
                PlatformAiUserUsage.model,
                func.sum(PlatformAiUserUsage.tokens),
            )
            .join(School, School.id == PlatformAiUserUsage.school_id)
            .group_by(PlatformAiUserUsage.school_id, School.name, PlatformAiUserUsage.model)
        )
    ).all()
    by_person = school_token_rows(
        [(school_id, name, model, int(tokens or 0)) for school_id, name, model, tokens in people]
    )
    person_by_school = {row["school_id"]: row for row in by_person}
    for row in daily:
        extra = person_by_school.get(row["school_id"])
        if extra is None or int(row.get("tokens") or 0) > 0:
            continue
        row["tokens"] = extra["tokens"]
        row["models"] = extra["models"]
    return daily


async def school_people_tokens(db: AsyncSession, school_id: UUID) -> dict:
    from src.modules.fees.tenant import set_rls_bypass

    await set_rls_bypass(db, True)
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
    usage_rows = (
        await db.execute(
            select(PlatformAiUserUsage.user_id, PlatformAiUserUsage.model, PlatformAiUserUsage.tokens).where(
                PlatformAiUserUsage.school_id == school_id
            )
        )
    ).all()
    by_user: dict[UUID, list[tuple[str | None, int]]] = {}
    for user_id, model, tokens in usage_rows:
        by_user.setdefault(user_id, []).append((model, int(tokens or 0)))

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
            }
        )

    assigned = sum(int(tokens or 0) for _user_id, _model, tokens in usage_rows)
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
        models = model_breakdown(by_user.get(person["user_id"], []))
        person["tokens"] = sum(item["tokens"] for item in models)
        person["models"] = models
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
