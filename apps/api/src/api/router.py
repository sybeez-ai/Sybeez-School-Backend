from fastapi import APIRouter

from src.core.constants import API_V1_PREFIX
from src.modules.access.router import router as access_router
from src.modules.academic_years.router import router as academic_years_router
from src.modules.achievements.router import router as achievements_router
from src.modules.ai_gateway.router import router as ai_gateway_router
from src.modules.analytics.router import router as analytics_router
from src.modules.announcements.router import router as announcements_router
from src.modules.assignments.router import router as assignments_router
from src.modules.attendance.router import router as attendance_router
from src.modules.audit.router import router as audit_router
from src.modules.auth.router import router as auth_router
from src.modules.classes.router import router as classes_router
from src.modules.documents.router import router as documents_router
from src.modules.enrollments.router import router as enrollments_router
from src.modules.exams.router import router as exams_router
from src.modules.grades.router import router as grades_router
from src.modules.health.router import router as health_router
from src.modules.internal.router import router as internal_router
from src.modules.homework.router import router as homework_router
from src.modules.invitations.router import router as invitations_router
from src.modules.learning_sessions.router import router as learning_sessions_router
from src.modules.messaging.router import router as messaging_router
from src.modules.notifications.router import router as notifications_router
from src.modules.parents.router import router as parents_router
from src.modules.platform.router import router as platform_router
from src.modules.progress.router import router as progress_router
from src.modules.questions.router import router as questions_router
from src.modules.reports.router import router as reports_router
from src.modules.results.router import router as results_router
from src.modules.schools.router import router as schools_router
from src.modules.student_parents.router import router as student_parents_router
from src.modules.students.router import router as students_router
from src.modules.subjects.router import router as subjects_router
from src.modules.subscriptions.router import router as subscriptions_router
from src.modules.syllabus.router import router as syllabus_router
from src.modules.teacher_assignments.router import router as teacher_assignments_router
from src.modules.teachers.router import router as teachers_router
from src.modules.teaching_records.router import router as teaching_records_router
from src.modules.timetable.router import router as timetable_router
from src.modules.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(internal_router)
api_router.include_router(ai_gateway_router, prefix=API_V1_PREFIX)
api_router.include_router(auth_router, prefix=API_V1_PREFIX)
api_router.include_router(access_router, prefix=API_V1_PREFIX)
api_router.include_router(platform_router, prefix=API_V1_PREFIX)
api_router.include_router(schools_router, prefix=API_V1_PREFIX)
api_router.include_router(users_router, prefix=API_V1_PREFIX)
api_router.include_router(invitations_router, prefix=API_V1_PREFIX)
api_router.include_router(subscriptions_router, prefix=API_V1_PREFIX)
api_router.include_router(academic_years_router, prefix=API_V1_PREFIX)
api_router.include_router(grades_router, prefix=API_V1_PREFIX)
api_router.include_router(classes_router, prefix=API_V1_PREFIX)
api_router.include_router(subjects_router, prefix=API_V1_PREFIX)
api_router.include_router(syllabus_router, prefix=API_V1_PREFIX)
api_router.include_router(teachers_router, prefix=API_V1_PREFIX)
api_router.include_router(students_router, prefix=API_V1_PREFIX)
api_router.include_router(parents_router, prefix=API_V1_PREFIX)
api_router.include_router(enrollments_router, prefix=API_V1_PREFIX)
api_router.include_router(student_parents_router, prefix=API_V1_PREFIX)
api_router.include_router(teacher_assignments_router, prefix=API_V1_PREFIX)
api_router.include_router(timetable_router, prefix=API_V1_PREFIX)
api_router.include_router(teaching_records_router, prefix=API_V1_PREFIX)
api_router.include_router(homework_router, prefix=API_V1_PREFIX)
api_router.include_router(assignments_router, prefix=API_V1_PREFIX)
api_router.include_router(attendance_router, prefix=API_V1_PREFIX)
api_router.include_router(exams_router, prefix=API_V1_PREFIX)
api_router.include_router(questions_router, prefix=API_V1_PREFIX)
api_router.include_router(results_router, prefix=API_V1_PREFIX)
api_router.include_router(progress_router, prefix=API_V1_PREFIX)
api_router.include_router(learning_sessions_router, prefix=API_V1_PREFIX)
api_router.include_router(achievements_router, prefix=API_V1_PREFIX)
api_router.include_router(documents_router, prefix=API_V1_PREFIX)
api_router.include_router(notifications_router, prefix=API_V1_PREFIX)
api_router.include_router(announcements_router, prefix=API_V1_PREFIX)
api_router.include_router(messaging_router, prefix=API_V1_PREFIX)
api_router.include_router(reports_router, prefix=API_V1_PREFIX)
api_router.include_router(analytics_router, prefix=API_V1_PREFIX)
api_router.include_router(audit_router, prefix=API_V1_PREFIX)
