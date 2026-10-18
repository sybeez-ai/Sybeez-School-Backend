"""Restrictable features. Code (not data) so a typo can never create a ghost feature.

``prefixes`` are API paths under /api/v1. ``portal.*`` keys switch off a whole portal (every route used by its roles).
"""

from src.shared.enums import UserRole

FEATURES: dict[str, dict] = {
    "portal.student": {"label": "Student portal", "portal": "student", "prefixes": []},
    "portal.teacher": {"label": "Teacher portal", "portal": "teacher", "prefixes": []},
    "portal.parent": {"label": "Parent portal", "portal": "parent", "prefixes": []},
    "portal.school": {"label": "School admin portal", "portal": "school", "prefixes": []},
    "portal.hrm": {"label": "HR portal", "portal": "hrm", "prefixes": []},
    "portal.finance": {"label": "Finance portal", "portal": "finance", "prefixes": []},
    "portal.staff": {"label": "Staff self-service portal", "portal": "staff", "prefixes": []},
    "online_tests": {"label": "Online tests", "portal": "all", "prefixes": ["online-tests", "online-test-attempts"]},
    "homework": {"label": "Homework and assignments", "portal": "all", "prefixes": ["homework", "assignments"]},
    "attendance": {"label": "Attendance", "portal": "all", "prefixes": ["attendance"]},
    "exams": {"label": "Exams and results", "portal": "all", "prefixes": ["exams", "results"]},
    "timetable": {"label": "Timetable", "portal": "all", "prefixes": ["timetable"]},
    "fees": {"label": "Fees", "portal": "all", "prefixes": ["fees", "payments"]},
    "finance": {"label": "Finance", "portal": "finance", "prefixes": ["finance"]},
    "payroll": {"label": "Payroll", "portal": "hrm", "prefixes": ["payroll"]},
    "hr": {"label": "HR management", "portal": "hrm", "prefixes": ["hr", "employees"]},
    "admissions": {"label": "Admissions", "portal": "school", "prefixes": ["admissions"]},
    "communication": {
        "label": "Announcements and messages",
        "portal": "all",
        "prefixes": ["communication", "announcements", "messaging", "parent-messages"],
    },
    "alerts": {"label": "WhatsApp and SMS alerts", "portal": "school", "prefixes": ["alerts"]},
    "ai_assistant": {"label": "AI assistant and AI tools", "portal": "all", "prefixes": ["ai", "ai-reports", "teacher-tools", "intelligence"]},
    "reading_buddy": {"label": "Reading buddy", "portal": "student", "prefixes": ["reading-buddy"]},
    "learning_paths": {
        "label": "Learning paths and progress",
        "portal": "student",
        "prefixes": ["learning-paths", "progress", "learning-sessions"],
    },
    "family_documents": {"label": "Family documents", "portal": "all", "prefixes": ["family-documents"]},
    "content_library": {"label": "Content library (board-tagged notes and question banks)", "portal": "all", "prefixes": ["content-library"]},
    "documents": {"label": "Document library", "portal": "all", "prefixes": ["documents"]},
    "analytics": {"label": "Analytics and reports", "portal": "school", "prefixes": ["analytics", "reports"]},
    "principal_ops": {"label": "Principal tools (cover and reports)", "portal": "school", "prefixes": ["ops"]},
    "bookstore": {"label": "Bookstore", "portal": "all", "prefixes": ["bookstore"]},
    "personal_life": {"label": "Diary, notes and personal goals", "portal": "all", "prefixes": ["diary", "notes", "personal-life"]},
}

PORTAL_FOR_ROLE: dict[str, str] = {
    UserRole.STUDENT.value: "portal.student",
    UserRole.TEACHER.value: "portal.teacher",
    UserRole.PARENT.value: "portal.parent",
    UserRole.SCHOOL_ADMIN.value: "portal.school",
    UserRole.HR.value: "portal.hrm",
    UserRole.HR_MANAGER.value: "portal.hrm",
    UserRole.PAYROLL_ADMIN.value: "portal.hrm",
    UserRole.PAYROLL_APPROVER.value: "portal.hrm",
    UserRole.ACCOUNTANT.value: "portal.finance",
    UserRole.CASHIER.value: "portal.finance",
    UserRole.AUDITOR.value: "portal.finance",
    UserRole.STAFF.value: "portal.staff",
}

# Never gated: sign-in, self profile, the feature map itself, platform and machine-to-machine routes.
EXEMPT_PREFIXES = (
    "auth", "features", "platform", "access/me", "schools/me", "users/health", "devices", "notifications",
    "invitations", "webhooks", "payments/webhook", "health",
)

_PREFIX_TO_KEY = {p: k for k, f in FEATURES.items() for p in f["prefixes"]}


def is_exempt(path: str) -> bool:
    rel = path.strip("/")
    return any(rel == e or rel.startswith(e + "/") for e in EXEMPT_PREFIXES)


def feature_for_path(path: str) -> str | None:
    """Feature key for an /api/v1 path (without the prefix), or None if it belongs to no named feature."""
    rel = path.strip("/")
    return _PREFIX_TO_KEY.get(rel.split("/", 1)[0])
