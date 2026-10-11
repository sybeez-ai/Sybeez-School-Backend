from src.shared.enums import UserRole

ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.PLATFORM_ADMIN: 100,
    UserRole.PLATFORM_STAFF: 90,
    UserRole.SCHOOL_ADMIN: 80,
    UserRole.HR: 55,
    UserRole.FINANCE: 50,
    UserRole.PAYROLL_APPROVER: 50,
    UserRole.TEACHER: 40,
    UserRole.PARENT: 30,
    UserRole.STUDENT: 20,
}

PLATFORM_ROLES = {UserRole.PLATFORM_ADMIN}
SCHOOL_ROLES = {
    UserRole.SCHOOL_ADMIN,
    UserRole.TEACHER,
    UserRole.STUDENT,
    UserRole.PARENT,
    UserRole.FINANCE,
    UserRole.HR,
    UserRole.PAYROLL_APPROVER,
}


def has_min_role(user_role: UserRole, required: UserRole) -> bool:
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required, 999)
