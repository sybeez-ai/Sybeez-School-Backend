from src.shared.enums import UserRole

ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.PLATFORM_ADMIN: 100,
    UserRole.SCHOOL_ADMIN: 80,
    UserRole.TEACHER: 60,
    UserRole.PARENT: 40,
    UserRole.STUDENT: 20,
}

PLATFORM_ROLES = {UserRole.PLATFORM_ADMIN}
SCHOOL_ROLES = {
    UserRole.SCHOOL_ADMIN,
    UserRole.TEACHER,
    UserRole.STUDENT,
    UserRole.PARENT,
}


def has_min_role(user_role: UserRole, required: UserRole) -> bool:
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required, 999)
