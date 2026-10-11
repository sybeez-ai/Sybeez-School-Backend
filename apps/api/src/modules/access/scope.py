"""Application-side visibility filters. Postgres RLS is the backstop."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.db.models import Enrollment, Parent, Student, StudentParent, Teacher, TeacherAssignment
from src.shared.enums import EnrollmentStatus, UserRole

_DIRECTORY = {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.FINANCE}
_CLASS_DIRECTORY = {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.FINANCE, UserRole.HR}


def _teacher_class_ids(user_id: UUID, school_id: UUID):
    return (
        select(TeacherAssignment.class_id)
        .join(Teacher, Teacher.id == TeacherAssignment.teacher_id)
        .where(Teacher.user_id == user_id, TeacherAssignment.school_id == school_id)
    )


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
    if user.role in _DIRECTORY:
        return stmt
    if user.role == UserRole.STUDENT:
        return stmt.where(Student.user_id == user.id)
    if user.role == UserRole.PARENT:
        return stmt.where(Student.id.in_(_child_ids(user.id, school_id)))
    if user.role == UserRole.TEACHER:
        return stmt.where(Student.id.in_(_teacher_student_ids(user.id, school_id)))
    return stmt.where(Student.id.is_(None))


def restrict_classes(stmt: Select, user: CurrentUser, school_id: UUID, class_column) -> Select:
    if user.role in _CLASS_DIRECTORY:
        return stmt
    if user.role == UserRole.TEACHER:
        return stmt.where(class_column.in_(_teacher_class_ids(user.id, school_id)))
    if user.role == UserRole.STUDENT:
        return stmt.where(class_column.in_(_student_class_ids(user.id, school_id)))
    if user.role == UserRole.PARENT:
        return stmt.where(class_column.in_(_parent_class_ids(user.id, school_id)))
    return stmt.where(class_column.is_(None))


def restrict_student_rows(stmt: Select, user: CurrentUser, school_id: UUID, student_column) -> Select:
    if user.role in {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN}:
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


async def same_school_user(db: AsyncSession, user_id: UUID) -> bool:
    from sqlalchemy import text

    result = await db.execute(text("SELECT rls_same_school_user(:uid)"), {"uid": user_id})
    return bool(result.scalar_one())
