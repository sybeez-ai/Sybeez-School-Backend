from src.shared.enums import UserRole

ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.PLATFORM_ADMIN: 100,
    UserRole.PLATFORM_STAFF: 90,
    UserRole.SCHOOL_ADMIN: 80,
    UserRole.ACCOUNTANT: 75,
    UserRole.CASHIER: 74,
    UserRole.AUDITOR: 73,
    UserRole.PAYROLL_ADMIN: 70,
    UserRole.HR_MANAGER: 68,
    UserRole.PAYROLL_APPROVER: 64,
    UserRole.TEACHER: 60,
    UserRole.STAFF: 55,
    UserRole.PARENT: 40,
    UserRole.STUDENT: 20,
}

PLATFORM_ROLES = {UserRole.PLATFORM_ADMIN}
SCHOOL_ROLES = {
    UserRole.SCHOOL_ADMIN,
    UserRole.PAYROLL_ADMIN,
    UserRole.HR_MANAGER,
    UserRole.ACCOUNTANT,
    UserRole.CASHIER,
    UserRole.PAYROLL_APPROVER,
    UserRole.AUDITOR,
    UserRole.TEACHER,
    UserRole.STUDENT,
    UserRole.PARENT,
    UserRole.FINANCE,
    UserRole.HR,
    UserRole.PAYROLL_APPROVER,
    UserRole.STAFF,
}


def has_min_role(user_role: UserRole, required: UserRole) -> bool:
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required, 999)
