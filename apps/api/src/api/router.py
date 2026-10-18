from fastapi import APIRouter, Depends

from src.core.constants import API_V1_PREFIX
from src.modules.access.router import router as access_router
from src.modules.academic_years.router import router as academic_years_router
from src.modules.alerts.router import admin_router as alerts_admin_router
from src.modules.alerts.router import webhook_router as alerts_webhook_router
from src.modules.achievements.router import router as achievements_router
from src.modules.admissions.router import router as admissions_router
from src.modules.ai_gateway.router import router as ai_gateway_router
from src.modules.ai_reports.router import router as ai_reports_router
from src.modules.analytics.router import router as analytics_router
from src.modules.announcements.router import router as announcements_router
from src.modules.assignments.router import router as assignments_router
from src.modules.attendance.router import router as attendance_router
from src.modules.audit.router import router as audit_router
from src.modules.auth.router import router as auth_router
from src.modules.bookstore.router import router as bookstore_router
from src.modules.classes.router import router as classes_router
from src.modules.communication.router import router as communication_router
from src.modules.diary.router import router as diary_router
from src.modules.documents.router import router as documents_router
from src.modules.employees.router import router as employees_router
from src.modules.enrollments.router import router as enrollments_router
from src.modules.exams.router import router as exams_router
from src.modules.fees.router import router as fees_router
from src.modules.finance.desk_router import desk as finance_desk_router
from src.modules.finance.expenses import router as finance_expenses_router
from src.modules.finance.router import router as finance_router
from src.modules.grades.router import router as grades_router
from src.modules.features.gate import feature_gate
from src.modules.features.router import me_router as features_me_router
from src.modules.features.router import platform_router as features_platform_router
from src.modules.health.router import router as health_router
from src.modules.homework.router import router as homework_router
from src.modules.hr.router import router as hr_router
from src.modules.hr_dashboard.router import router as hr_dashboard_router
from src.modules.hr_files.router import router as hr_files_router
from src.modules.hr_leave_policy.router import router as hr_leave_policy_router
from src.modules.hr_letters.router import router as hr_letters_router
from src.modules.hr_operations.router import router as hr_operations_router
from src.modules.hr_org.router import router as hr_org_router
from src.modules.hr_requests.router import router as hr_requests_router
from src.modules.hr_settings.router import router as hr_settings_router
from src.modules.hr_workflows.router import router as hr_workflows_router
from src.modules.intelligence.router import router as intelligence_router
from src.modules.internal.router import router as internal_router
from src.modules.invitations.router import router as invitations_router
from src.modules.learning_sessions.router import router as learning_sessions_router
from src.modules.messaging.router import router as messaging_router
from src.modules.notes.router import router as notes_router
from src.modules.online_tests.router import attempts_router as online_test_attempts_router
from src.modules.online_tests.router import router as online_tests_router
from src.modules.notifications.router import router as notifications_router
from src.modules.parent_messages.router import router as parent_messages_router
from src.modules.reading_buddy.router import router as reading_buddy_router
from src.modules.school_ops.router import router as school_ops_router
from src.modules.family_documents.router import router as family_documents_router
from src.modules.learning_paths.router import router as learning_paths_router
from src.modules.teacher_tools.router import router as teacher_tools_router
from src.modules.parents.router import router as parents_router
from src.modules.personal_life.router import router as personal_life_router
from src.modules.platform.router import router as platform_router
from src.modules.progress.router import router as progress_router
from src.modules.questions.router import router as questions_router
from src.modules.reports.router import router as reports_router
from src.modules.results.router import router as results_router
from src.modules.school_admin.router import router as school_admin_router
from src.modules.schools.router import router as schools_router
from src.modules.staff_portal.router import router as staff_portal_router
from src.modules.payroll.assistant.router import router as payroll_assistant_router
from src.modules.payroll.router import router as payroll_router
from src.modules.staff_attendance.router import router as staff_attendance_router
from src.modules.staff_attendance.self_router import router as staff_self_attendance_router
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
from src.modules.devices.router import router as devices_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(internal_router)
api_router.include_router(ai_gateway_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(auth_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(devices_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(online_tests_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(online_test_attempts_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(access_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(platform_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(schools_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(users_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(invitations_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(subscriptions_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(academic_years_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(grades_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(classes_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(subjects_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(syllabus_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(teachers_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(students_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(parents_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(enrollments_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(student_parents_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(teacher_assignments_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(timetable_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(teaching_records_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(homework_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(assignments_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(attendance_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(exams_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(fees_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(finance_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(finance_expenses_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(finance_desk_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(school_admin_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(staff_portal_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(diary_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(notes_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(personal_life_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(bookstore_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(questions_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(results_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(progress_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(learning_sessions_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(achievements_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(documents_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(notifications_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(communication_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(alerts_admin_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(alerts_webhook_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(admissions_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(employees_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_org_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(payroll_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(payroll_assistant_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(staff_self_attendance_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(staff_attendance_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_letters_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_files_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_workflows_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_requests_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_settings_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_leave_policy_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_dashboard_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(hr_operations_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(intelligence_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(announcements_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(messaging_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(reports_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(analytics_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(audit_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(ai_reports_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(parent_messages_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(reading_buddy_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(school_ops_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(teacher_tools_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(learning_paths_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(family_documents_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(features_me_router, prefix=API_V1_PREFIX, dependencies=[Depends(feature_gate)])
api_router.include_router(features_platform_router, prefix=API_V1_PREFIX)
