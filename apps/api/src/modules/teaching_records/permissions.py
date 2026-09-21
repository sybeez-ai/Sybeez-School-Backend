from src.shared.enums import UserRole

TEACHER_WRITE = (UserRole.TEACHER, UserRole.SCHOOL_ADMIN)
STUDENT_READ = (UserRole.STUDENT, UserRole.TEACHER, UserRole.SCHOOL_ADMIN, UserRole.PARENT)
