"""Canonical permission catalog and default role matrix.

School admins may grant or revoke individual permissions on top of these defaults.
Platform administration is not grantable by a school admin.
Finance, HR, and payroll codes are declared here so those modules can enforce the
same names; this branch does not implement their business endpoints.
"""

from src.shared.enums import UserRole

PERMISSIONS: dict[str, str] = {
    "session.read": "Read the signed-in profile and effective permissions",
    "platform.manage": "Manage schools and platform operators",
    "schools.read": "Read the current school profile",
    "access.read": "Read the role matrix and membership permissions",
    "access.invite": "Invite a user into the school with a role",
    "access.grant": "Grant a role or an extra permission",
    "access.revoke": "Revoke a role or a permission",
    "audit.read": "Read the school audit log",
    "users.read": "Read school user accounts",
    "invitations.read": "Read pending invitations",
    "academic_years.read": "Read academic years",
    "academic_years.write": "Create academic years",
    "grades.read": "Read grades",
    "grades.write": "Create grades",
    "classes.read": "Read classes",
    "classes.write": "Create classes",
    "subjects.read": "Read subjects",
    "subjects.write": "Create subjects",
    "syllabus.read": "Read syllabus",
    "enrollments.read": "Read class enrollments",
    "enrollments.write": "Create enrollments",
    "teacher_assignments.read": "Read teacher class assignments",
    "teacher_assignments.write": "Assign teachers to classes",
    "teaching_records.read": "Read teaching records",
    "teaching_records.write": "Write teaching records",
    "students.read": "Read student profiles",
    "students.write": "Create and update students",
    "teachers.read": "Read teacher profiles",
    "teachers.write": "Create and update teachers",
    "parents.read": "Read parent profiles",
    "parents.write": "Create parents and link children",
    "student_parents.read": "Read student-parent links",
    "student_parents.write": "Link students and parents",
    "attendance.read": "Read attendance",
    "attendance.write": "Mark attendance",
    "homework.read": "Read homework",
    "homework.write": "Create and update homework",
    "homework.submit": "Submit homework",
    "homework.grade": "Grade homework",
    "assignments.read": "Read assignments",
    "assignments.write": "Create and update assignments",
    "assignments.submit": "Submit assignments",
    "assignments.grade": "Grade assignments",
    "exams.read": "Read exams",
    "exams.write": "Create and update exams",
    "results.read": "Read exam results",
    "results.write": "Record exam results",
    "online_tests.read": "View online MCQ tests and their results",
    "online_tests.write": "Create, edit, publish and review online MCQ tests",
    "online_tests.take": "Take online MCQ tests",
    "online_tests.manage": "Turn online tests on or off for the school and set the AI monthly cap",
    "timetable.read": "Read the timetable",
    "timetable.write": "Edit the timetable",
    "announcements.read": "Read announcements",
    "announcements.write": "Publish announcements",
    "messages.read": "Read messages",
    "messages.write": "Send messages",
    "documents.read": "Read documents",
    "questions.read": "Read question bank",
    "questions.write": "Write question bank",
    "reports.read": "Read reports",
    "analytics.read": "Read analytics",
    "progress.read": "Read progress",
    "achievements.read": "Read achievements",
    "notifications.read": "Read notifications",
    "learning_sessions.read": "Read learning sessions",
    "subscriptions.read": "Read the school subscription",
    "ai.use": "Use the AI teacher gateway",
    "finance.read": "Read finance records (owned by the finance module)",
    "finance.manage": "Manage finance records (owned by the finance module)",
    "hr.read": "Read HR records (owned by the HR module)",
    "hr.manage": "Manage HR records (owned by the HR module)",
    "payroll.read": "Read payroll records (owned by the payroll module)",
    "payroll.approve": "Approve payroll (owned by the payroll module)",
}

ALL_PERMISSIONS: frozenset[str] = frozenset(PERMISSIONS)

_SCHOOL_ADMIN = ALL_PERMISSIONS - {"platform.manage"}

_TEACHER = frozenset(
    {
        "session.read",
        "schools.read",
        "academic_years.read",
        "grades.read",
        "classes.read",
        "subjects.read",
        "syllabus.read",
        "enrollments.read",
        "teacher_assignments.read",
        "teaching_records.read",
        "teaching_records.write",
        "students.read",
        "teachers.read",
        "parents.read",
        "student_parents.read",
        "attendance.read",
        "attendance.write",
        "homework.read",
        "homework.write",
        "homework.grade",
        "assignments.read",
        "assignments.write",
        "assignments.grade",
        "exams.read",
        "online_tests.read",
        "online_tests.write",
        "exams.write",
        "results.read",
        "results.write",
        "timetable.read",
        "announcements.read",
        "announcements.write",
        "messages.read",
        "messages.write",
        "documents.read",
        "questions.read",
        "questions.write",
        "progress.read",
        "achievements.read",
        "notifications.read",
        "learning_sessions.read",
        "ai.use",
    }
)

_STUDENT = frozenset(
    {
        "session.read",
        "schools.read",
        "academic_years.read",
        "grades.read",
        "classes.read",
        "subjects.read",
        "syllabus.read",
        "students.read",
        "teachers.read",
        "attendance.read",
        "homework.read",
        "homework.submit",
        "assignments.read",
        "assignments.submit",
        "online_tests.take",
        "exams.read",
        "results.read",
        "timetable.read",
        "announcements.read",
        "messages.read",
        "messages.write",
        "progress.read",
        "achievements.read",
        "notifications.read",
        "learning_sessions.read",
        "ai.use",
    }
)

_PARENT = frozenset(
    {
        "session.read",
        "schools.read",
        "academic_years.read",
        "grades.read",
        "classes.read",
        "subjects.read",
        "syllabus.read",
        "students.read",
        "teachers.read",
        "parents.read",
        "student_parents.read",
        "attendance.read",
        "homework.read",
        "assignments.read",
        "online_tests.read",
        "exams.read",
        "results.read",
        "timetable.read",
        "announcements.read",
        "messages.read",
        "messages.write",
        "progress.read",
        "achievements.read",
        "notifications.read",
        "ai.use",
    }
)

_FINANCE = frozenset(
    {
        "session.read",
        "schools.read",
        "notifications.read",
        "announcements.read",
        "students.read",
        "parents.read",
        "classes.read",
        "finance.read",
        "finance.manage",
        "payroll.read",
    }
)

_HR = frozenset(
    {
        "session.read",
        "schools.read",
        "notifications.read",
        "announcements.read",
        "users.read",
        "teachers.read",
        "teachers.write",
        "classes.read",
        "academic_years.read",
        "timetable.read",
        "hr.read",
        "hr.manage",
        "payroll.read",
    }
)

_PAYROLL = frozenset(
    {
        "session.read",
        "schools.read",
        "notifications.read",
        "announcements.read",
        "teachers.read",
        "payroll.read",
        "payroll.approve",
    }
)

# Internal staff portals. These are not school-admin defaults. The platform portal
# does not open /platform routes; those stay limited to the bootstrapped super admin.
PORTALS: dict[str, frozenset[str]] = {
    "platform": frozenset({"session.read"}),
    "finance": frozenset(
        {
            "session.read",
            "schools.read",
            "notifications.read",
            "announcements.read",
            "students.read",
            "parents.read",
            "classes.read",
            "finance.read",
            "finance.manage",
            "payroll.read",
        }
    ),
    "payroll": frozenset(
        {
            "session.read",
            "schools.read",
            "notifications.read",
            "announcements.read",
            "teachers.read",
            "payroll.read",
            "payroll.approve",
        }
    ),
    "hrm": frozenset(
        {
            "session.read",
            "schools.read",
            "notifications.read",
            "announcements.read",
            "users.read",
            "teachers.read",
            "teachers.write",
            "classes.read",
            "academic_years.read",
            "timetable.read",
            "hr.read",
            "hr.manage",
            "payroll.read",
        }
    ),
    "school_admin": _SCHOOL_ADMIN,
}

_ACCOUNTANT = _FINANCE
_CASHIER = frozenset(
    {
        "session.read",
        "schools.read",
        "notifications.read",
        "announcements.read",
        "students.read",
        "parents.read",
        "classes.read",
        "finance.read",
        "finance.manage",
    }
)
_AUDITOR = frozenset(
    {
        "session.read",
        "schools.read",
        "notifications.read",
        "announcements.read",
        "students.read",
        "parents.read",
        "classes.read",
        "finance.read",
        "payroll.read",
    }
)
_PAYROLL_ADMIN = _PAYROLL | frozenset({"hr.read", "users.read", "teachers.write"})

# Non-teaching employees sign in for their own HR records. School HR data stays
# behind hr.read / hr.manage, which a school admin can grant per person.
_STAFF = frozenset(
    {
        "session.read",
        "schools.read",
        "notifications.read",
        "announcements.read",
    }
)

ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.PLATFORM_ADMIN: ALL_PERMISSIONS,
    UserRole.PLATFORM_STAFF: frozenset(),
    UserRole.SCHOOL_ADMIN: _SCHOOL_ADMIN,
    UserRole.PAYROLL_ADMIN: _PAYROLL_ADMIN,
    UserRole.HR_MANAGER: _HR,
    UserRole.ACCOUNTANT: _ACCOUNTANT,
    UserRole.CASHIER: _CASHIER,
    UserRole.PAYROLL_APPROVER: _PAYROLL,
    UserRole.AUDITOR: _AUDITOR,
    UserRole.TEACHER: _TEACHER,
    UserRole.STUDENT: _STUDENT,
    UserRole.PARENT: _PARENT,
    UserRole.FINANCE: _FINANCE,
    UserRole.HR: _HR,
    UserRole.STAFF: _STAFF,
}

# Roles a school admin may invite or assign. Platform admin is not one of them.
SCHOOL_ASSIGNABLE_ROLES: frozenset[UserRole] = frozenset(
    {
        UserRole.SCHOOL_ADMIN,
        UserRole.TEACHER,
        UserRole.STUDENT,
        UserRole.PARENT,
        UserRole.FINANCE,
        UserRole.HR,
        UserRole.HR_MANAGER,
        UserRole.ACCOUNTANT,
        UserRole.CASHIER,
        UserRole.AUDITOR,
        UserRole.PAYROLL_ADMIN,
        UserRole.PAYROLL_APPROVER,
        UserRole.STAFF,
    }
)

GRANT_EFFECTS = frozenset({"GRANT", "REVOKE"})


def permissions_for(role: UserRole | str) -> frozenset[str]:
    try:
        key = role if isinstance(role, UserRole) else UserRole(role)
    except ValueError:
        return frozenset()
    return ROLE_PERMISSIONS.get(key, frozenset())
