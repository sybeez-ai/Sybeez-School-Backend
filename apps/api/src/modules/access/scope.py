"""Application-side visibility filters. Postgres RLS is the backstop."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError
from src.db.models import (
    ClassRoom,
    Enrollment,
    Parent,
    SchoolMembership,
    Student,
    StudentParent,
    Teacher,
    TeacherAssignment,
)
from src.shared.enums import EnrollmentStatus, MembershipStatus, UserRole

_DIRECTORY = {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.FINANCE}
_CLASS_DIRECTORY = {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.FINANCE, UserRole.HR}


def _staff_portals(user: CurrentUser, names: set[str]) -> bool:
    return user.role == UserRole.PLATFORM_STAFF and bool(names.intersection(user.portals))


def _teacher_class_ids(user_id: UUID, school_id: UUID):
    assigned = (
        select(TeacherAssignment.class_id)
        .join(Teacher, Teacher.id == TeacherAssignment.teacher_id)
        .where(Teacher.user_id == user_id, TeacherAssignment.school_id == school_id)
    )
    led = (
        select(ClassRoom.id)
        .join(Teacher, Teacher.id == ClassRoom.class_teacher_id)
        .where(Teacher.user_id == user_id, ClassRoom.school_id == school_id)
    )
    return assigned.union(led)


def _student_class_ids(user_id: UUID, school_id: UUID):
    return (
        select(Enrollment.class_id)
        .join(Student, Student.id == Enrollment.student_id)
        .where(
            Student.user_id == user_id,
            Enrollment.school_id == school_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
        )
    )


def _parent_class_ids(user_id: UUID, school_id: UUID):
    return (
        select(Enrollment.class_id)
        .join(StudentParent, StudentParent.student_id == Enrollment.student_id)
        .join(Parent, Parent.id == StudentParent.parent_id)
        .where(
            Parent.user_id == user_id,
            Enrollment.school_id == school_id,
            Enrollment.status == EnrollmentStatus.ACTIVE.value,
        )
    )


def _child_ids(user_id: UUID, school_id: UUID):
    return (
        select(StudentParent.student_id)
        .join(Parent, Parent.id == StudentParent.parent_id)
        .where(Parent.user_id == user_id, StudentParent.school_id == school_id)
    )


def _teacher_student_ids(user_id: UUID, school_id: UUID):
    return select(Enrollment.student_id).where(
        Enrollment.school_id == school_id,
        Enrollment.status == EnrollmentStatus.ACTIVE.value,
        Enrollment.class_id.in_(_teacher_class_ids(user_id, school_id)),
    )


def restrict_students(stmt: Select, user: CurrentUser, school_id: UUID) -> Select:
    if user.role in _DIRECTORY or _staff_portals(user, {"finance", "school_admin"}):
        return stmt
    if user.role == UserRole.STUDENT:
        return stmt.where(Student.user_id == user.id)
    if user.role == UserRole.PARENT:
        return stmt.where(Student.id.in_(_child_ids(user.id, school_id)))
    if user.role == UserRole.TEACHER:
        return stmt.where(Student.id.in_(_teacher_student_ids(user.id, school_id)))
    return stmt.where(Student.id.is_(None))


def restrict_classes(stmt: Select, user: CurrentUser, school_id: UUID, class_column) -> Select:
    if user.role in _CLASS_DIRECTORY or _staff_portals(user, {"finance", "hrm", "school_admin"}):
        return stmt
    if user.role == UserRole.TEACHER:
        return stmt.where(class_column.in_(_teacher_class_ids(user.id, school_id)))
    if user.role == UserRole.STUDENT:
        return stmt.where(class_column.in_(_student_class_ids(user.id, school_id)))
    if user.role == UserRole.PARENT:
        return stmt.where(class_column.in_(_parent_class_ids(user.id, school_id)))
    return stmt.where(class_column.is_(None))


def restrict_student_rows(stmt: Select, user: CurrentUser, school_id: UUID, student_column) -> Select:
    if user.role in {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN} or _staff_portals(user, {"school_admin"}):
        return stmt
    if user.role == UserRole.TEACHER:
        return stmt.where(student_column.in_(_teacher_student_ids(user.id, school_id)))
    if user.role == UserRole.STUDENT:
        return stmt.where(
            student_column.in_(select(Student.id).where(Student.user_id == user.id, Student.school_id == school_id))
        )
    if user.role == UserRole.PARENT:
        return stmt.where(student_column.in_(_child_ids(user.id, school_id)))
    return stmt.where(student_column.is_(None))


async def teacher_class_ids(db: AsyncSession, user_id: UUID, school_id: UUID) -> set[UUID]:
    """Classes a teacher is assigned to or leads as class teacher."""
    assigned = (
        await db.execute(
            select(TeacherAssignment.class_id)
            .join(Teacher, Teacher.id == TeacherAssignment.teacher_id)
            .where(Teacher.user_id == user_id, TeacherAssignment.school_id == school_id)
        )
    ).scalars().all()
    led = (
        await db.execute(
            select(ClassRoom.id)
            .join(Teacher, Teacher.id == ClassRoom.class_teacher_id)
            .where(Teacher.user_id == user_id, ClassRoom.school_id == school_id)
        )
    ).scalars().all()
    return set(assigned) | set(led)


async def teaches_class(
    db: AsyncSession,
    school_id: UUID,
    class_id: UUID,
    *,
    user_id: UUID | None = None,
    teacher_id: UUID | None = None,
) -> bool:
    if user_id is not None:
        return class_id in await teacher_class_ids(db, user_id, school_id)
    if teacher_id is None:
        return False
    assigned = (
        await db.execute(
            select(TeacherAssignment.id).where(
                TeacherAssignment.school_id == school_id,
                TeacherAssignment.teacher_id == teacher_id,
                TeacherAssignment.class_id == class_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if assigned is not None:
        return True
    led = (
        await db.execute(
            select(ClassRoom.id).where(
                ClassRoom.school_id == school_id,
                ClassRoom.id == class_id,
                ClassRoom.class_teacher_id == teacher_id,
            )
        )
    ).scalar_one_or_none()
    return led is not None


async def assert_teacher_class(db: AsyncSession, user_id: UUID, school_id: UUID, class_id: UUID) -> None:
    if class_id not in await teacher_class_ids(db, user_id, school_id):
        raise ForbiddenError("Not your class")


async def assert_teacher_student(db: AsyncSession, user_id: UUID, school_id: UUID, student_id: UUID) -> None:
    class_id = (
        await db.execute(
            select(Enrollment.class_id).where(
                Enrollment.school_id == school_id,
                Enrollment.student_id == student_id,
                Enrollment.status == EnrollmentStatus.ACTIVE.value,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if class_id is None or class_id not in await teacher_class_ids(db, user_id, school_id):
        raise ForbiddenError("Not your student")


async def same_school_user(db: AsyncSession, user_id: UUID) -> bool:
    from sqlalchemy import text

    school = (await db.execute(text("SELECT current_setting('app.school_id', true)"))).scalar_one()
    if not school:
        return False
    result = await db.execute(
        select(SchoolMembership.id).where(
            SchoolMembership.user_id == user_id,
            SchoolMembership.school_id == UUID(school),
            SchoolMembership.status == MembershipStatus.ACTIVE.value,
        )
    )
    return result.scalar_one_or_none() is not None
