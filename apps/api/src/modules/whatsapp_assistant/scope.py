"""Application-level scope for WhatsApp tools.

Postgres row-level security is applied as well, by binding the session to this
user, school, and role before a tool runs. These rules are what the tests pin:
a tool cannot widen the caller's school, children, classes, or staff record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

_SCHOOL_WIDE = frozenset({"SCHOOL_ADMIN", "PLATFORM_ADMIN"})
_READ = {
    "attendance": "attendance.read",
    "results": "results.read",
    "homework": "homework.read",
    "timetable": "timetable.read",
    "announcements": "announcements.read",
    "lesson_material": "ai.use",
}


@dataclass(frozen=True)
class DataScope:
    user_id: uuid.UUID
    school_id: uuid.UUID
    role: str
    permissions: frozenset[str]
    student_ids: frozenset[uuid.UUID]
    class_ids: frozenset[uuid.UUID]
    selected_student_id: uuid.UUID | None = None
    employee_id: uuid.UUID | None = None
    teacher_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ToolQuery:
    tool: str
    school_id: uuid.UUID
    user_id: uuid.UUID
    role: str = ""
    denied: str | None = None
    student_ids: frozenset[uuid.UUID] | None = None
    class_ids: frozenset[uuid.UUID] | None = None
    own_only: bool = True
    employee_id: uuid.UUID | None = None
    teacher_id: uuid.UUID | None = None
    search_name: str | None = None
    published_only: bool = True


def _uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _denied(tool: str, scope: DataScope, reason: str) -> ToolQuery:
    return ToolQuery(tool=tool, school_id=scope.school_id, user_id=scope.user_id, role=scope.role, denied=reason)


def _has(scope: DataScope, code: str) -> bool:
    return code in scope.permissions or scope.role in _SCHOOL_WIDE


def transcript_school(
    *,
    actor_role: str,
    actor_school: uuid.UUID | None,
    requested: uuid.UUID | None,
    is_platform_admin: bool,
) -> uuid.UUID | None:
    """None means every school, which only a platform admin may ask for."""
    if is_platform_admin or actor_role == "PLATFORM_ADMIN":
        return requested
    if actor_school is None:
        raise PermissionError("school required")
    if requested is not None and requested != actor_school:
        raise PermissionError("cross school")
    return actor_school


def _student_filter(scope: DataScope, requested: uuid.UUID | None, *, school_wide: bool) -> tuple[frozenset[uuid.UUID] | None, str | None]:
    if school_wide:
        return (frozenset({requested}) if requested else None), None
    allowed = scope.student_ids
    if scope.selected_student_id is not None:
        allowed = allowed & {scope.selected_student_id}
    if requested is not None and requested not in allowed:
        return None, "That student is not on your account."
    if not allowed:
        return None, "I can't find a student on your account."
    if requested is not None:
        allowed = frozenset({requested})
    return allowed, None


def _class_filter(scope: DataScope, *, school_wide: bool) -> tuple[frozenset[uuid.UUID] | None, str | None]:
    if school_wide:
        return None, None
    if scope.role == "TEACHER":
        if not scope.class_ids:
            return None, "I can only see classes assigned to you, and none are assigned."
        return scope.class_ids, None
    if not scope.class_ids:
        return None, "I can't find a class on your account."
    return scope.class_ids, None


def build_tool_query(tool: str, scope: DataScope, arguments: dict | None, *, leave_enabled: bool = False) -> ToolQuery:
    """Build the only filter a tool is allowed to use. Arguments cannot change the school."""
    args = arguments or {}
    requested = _uuid(args.get("student_id"))
    school_wide = scope.role in _SCHOOL_WIDE
    name = str(args.get("employee_name") or "").strip()

    if tool in {"attendance", "results"}:
        if not _has(scope, _READ[tool]):
            return _denied(tool, scope, "Your account can't look that up.")
        if scope.role == "TEACHER" and not school_wide:
            if not scope.class_ids:
                return _denied(tool, scope, "I can only see classes assigned to you, and none are assigned.")
            return ToolQuery(
                tool=tool,
                school_id=scope.school_id,
                user_id=scope.user_id,
                role=scope.role,
                student_ids=frozenset({requested}) if requested else None,
                class_ids=scope.class_ids,
                published_only=False,
            )
        students, reason = _student_filter(scope, requested, school_wide=school_wide)
        if reason:
            return _denied(tool, scope, reason)
        return ToolQuery(
            tool=tool,
            school_id=scope.school_id,
            user_id=scope.user_id,
            role=scope.role,
            student_ids=students,
            published_only=scope.role in {"PARENT", "STUDENT"},
        )

    if tool in {"homework", "timetable", "lesson_material", "announcements"}:
        if tool == "lesson_material" and scope.role in {"STUDENT", "PARENT", "TEACHER"}:
            allowed = True
        else:
            allowed = _has(scope, _READ[tool])
        if not allowed:
            return _denied(tool, scope, "Your account can't look that up.")
        classes, reason = _class_filter(scope, school_wide=school_wide)
        if reason:
            return _denied(tool, scope, reason)
        return ToolQuery(tool=tool, school_id=scope.school_id, user_id=scope.user_id, role=scope.role, class_ids=classes)

    if tool in {"fees_due", "fee_receipts"}:
        finance = _has(scope, "finance.read") or "finance.manage" in scope.permissions
        if finance or school_wide:
            students = frozenset({requested}) if requested else None
            return ToolQuery(
                tool=tool,
                school_id=scope.school_id,
                user_id=scope.user_id,
                role=scope.role,
                student_ids=students,
                own_only=False,
            )
        if scope.role not in {"PARENT", "STUDENT"}:
            return _denied(tool, scope, "Your account can't look up fees.")
        students, reason = _student_filter(scope, requested, school_wide=False)
        if reason:
            return _denied(tool, scope, reason)
        return ToolQuery(tool=tool, school_id=scope.school_id, user_id=scope.user_id, role=scope.role, student_ids=students)

    if tool == "payslips":
        if name and "payroll.read" in scope.permissions:
            return ToolQuery(
                tool=tool,
                school_id=scope.school_id,
                user_id=scope.user_id,
                role=scope.role,
                own_only=False,
                search_name=name,
            )
        if scope.employee_id is None and scope.teacher_id is None:
            return _denied(tool, scope, "I can't find a payslip on your account.")
        return ToolQuery(
            tool=tool,
            school_id=scope.school_id,
            user_id=scope.user_id,
            role=scope.role,
            own_only=True,
            employee_id=scope.employee_id,
            teacher_id=scope.teacher_id,
        )

    if tool == "leave_balance":
        if name and ("hr.read" in scope.permissions or "hr.manage" in scope.permissions):
            return ToolQuery(
                tool=tool,
                school_id=scope.school_id,
                user_id=scope.user_id,
                role=scope.role,
                own_only=False,
                search_name=name,
            )
        return ToolQuery(tool=tool, school_id=scope.school_id, user_id=scope.user_id, role=scope.role, own_only=True)

    if tool == "leave_request":
        if not leave_enabled:
            return _denied(tool, scope, "Leave requests on WhatsApp are turned off.")
        if scope.role in {"PARENT", "STUDENT"}:
            return _denied(tool, scope, "Leave requests on WhatsApp are for staff accounts.")
        return ToolQuery(tool=tool, school_id=scope.school_id, user_id=scope.user_id, role=scope.role, own_only=True)

    return _denied(tool, scope, "That action is not available.")
