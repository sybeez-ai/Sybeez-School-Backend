"""Role defaults are an explicit contract, not a copy of the catalog dict."""

from src.modules.access.catalog import ALL_PERMISSIONS, ROLE_PERMISSIONS, permissions_for
from src.shared.enums import UserRole


def test_catalog_covers_every_role_and_unknown_roles_get_nothing():
    assert set(ROLE_PERMISSIONS) == set(UserRole)
    assert permissions_for("NOT_A_ROLE") == frozenset()
    for role, granted in ROLE_PERMISSIONS.items():
        assert granted <= ALL_PERMISSIONS
        assert granted == permissions_for(role)


def test_platform_and_school_admin_split():
    assert permissions_for(UserRole.PLATFORM_ADMIN) == ALL_PERMISSIONS
    assert "platform.manage" in permissions_for(UserRole.PLATFORM_ADMIN)
    school = permissions_for(UserRole.SCHOOL_ADMIN)
    assert school == ALL_PERMISSIONS - {"platform.manage"}
    assert "finance.manage" in school
    assert "hr.manage" in school
    assert "payroll.approve" in school
    assert "access.grant" in school and "access.revoke" in school


def test_teacher_can_run_class_academics_and_not_administration():
    granted = permissions_for(UserRole.TEACHER)
    assert {
        "attendance.read",
        "attendance.write",
        "homework.read",
        "homework.write",
        "homework.grade",
        "assignments.read",
        "assignments.write",
        "assignments.grade",
        "exams.read",
        "exams.write",
        "results.read",
        "results.write",
        "students.read",
        "timetable.read",
        "announcements.write",
        "messages.write",
    } <= granted
    assert granted.isdisjoint(
        {
            "homework.submit",
            "assignments.submit",
            "students.write",
            "timetable.write",
            "access.invite",
            "access.grant",
            "access.revoke",
            "platform.manage",
            "finance.read",
            "finance.manage",
            "hr.read",
            "hr.manage",
            "payroll.read",
            "payroll.approve",
            "audit.read",
        }
    )


def test_student_reads_own_work_and_cannot_record_it():
    granted = permissions_for(UserRole.STUDENT)
    assert {
        "students.read",
        "attendance.read",
        "homework.read",
        "homework.submit",
        "assignments.read",
        "assignments.submit",
        "exams.read",
        "results.read",
        "timetable.read",
        "messages.read",
        "messages.write",
    } <= granted
    assert granted.isdisjoint(
        {
            "attendance.write",
            "homework.write",
            "homework.grade",
            "assignments.write",
            "exams.write",
            "results.write",
            "parents.read",
            "announcements.write",
            "access.grant",
            "finance.read",
            "hr.read",
            "payroll.read",
        }
    )


def test_parent_reads_children_and_cannot_submit_or_mark():
    granted = permissions_for(UserRole.PARENT)
    assert {
        "students.read",
        "parents.read",
        "student_parents.read",
        "attendance.read",
        "homework.read",
        "assignments.read",
        "exams.read",
        "results.read",
        "messages.write",
    } <= granted
    assert granted.isdisjoint(
        {
            "homework.submit",
            "assignments.submit",
            "attendance.write",
            "results.write",
            "exams.write",
            "homework.grade",
            "access.grant",
        }
    )


def test_finance_sees_billing_directory_not_academics_or_payroll_approval():
    granted = permissions_for(UserRole.FINANCE)
    assert {"students.read", "parents.read", "classes.read", "finance.read", "finance.manage", "payroll.read"} <= granted
    assert granted.isdisjoint(
        {
            "results.read",
            "exams.read",
            "attendance.read",
            "homework.read",
            "teachers.read",
            "payroll.approve",
            "hr.manage",
            "students.write",
        }
    )


def test_hr_manages_staff_not_students_or_payroll_approval():
    granted = permissions_for(UserRole.HR)
    assert {"teachers.read", "teachers.write", "hr.read", "hr.manage", "payroll.read", "timetable.read", "users.read"} <= granted
    assert granted.isdisjoint({"students.read", "payroll.approve", "results.read", "attendance.write", "finance.manage"})


def test_payroll_approver_is_narrow():
    granted = permissions_for(UserRole.PAYROLL_APPROVER)
    assert {"teachers.read", "payroll.read", "payroll.approve", "announcements.read"} <= granted
    assert granted.isdisjoint(
        {
            "students.read",
            "hr.manage",
            "hr.read",
            "teachers.write",
            "finance.manage",
            "results.read",
            "attendance.read",
        }
    )
