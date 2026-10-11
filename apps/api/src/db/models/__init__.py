from src.db.models.academic_year import AcademicYear
from src.db.models.ai_conversation import AIConversation, AIMessage
from src.db.models.attendance import Attendance, AttendanceAuditLog, AttendanceSettings
from src.db.models.attendance_digest_log import AttendanceDigestLog
from src.db.models.audit_log import AuditLog, SecurityEvent
from src.db.models.bookstore import BookstoreItem, BookstoreOrder, BookstoreWishlist
from src.db.models.leave_request import LeaveRequest
from src.db.models.class_ import ClassRoom
from src.db.models.communication import (
    Achievement,
    Announcement,
    AnnouncementAudience,
    CommunicationAuditLog,
    Conversation,
    ConversationMessage,
    ConversationParticipant,
    Message,
)
from src.db.models.document import Document
from src.db.models.diary import (
    DiaryEntry,
    PersonalGoal,
    PersonalGoalMilestone,
    PersonalHabit,
    PersonalHabitCheckin,
)
from src.db.models.personal_note import PersonalNote
from src.db.models.enrollment import Enrollment
from src.db.models.exam import (
    Exam,
    ExamSchedule,
    ExamSection,
    ExamSubject,
    ExamType,
    GradeRule,
    GradingScale,
    MarksEntry,
    ResultAuditLog,
    StudentMark,
)
from src.db.models.fee import (
    FeeAdjustment,
    FeeAuditLog,
    FeeCategory,
    FeeInstallment,
    FeeInvoice,
    FeeInvoiceItem,
    FeeReceipt,
    FeeSequence,
    FeeSettings,
    FeeStructure,
    FeeStructureItem,
    LateFeeRule,
    Payment,
    PaymentAllocation,
    PaymentRefund,
    StudentFeeAccount,
)
from src.db.models.fee_reminder_log import FeeReminderLog
from src.db.models.grade import Grade
from src.db.models.homework import (
    Homework,
    HomeworkAttachment,
    HomeworkSubmission,
    SubmissionAttachment,
)
from src.db.models.hr import (
    Department,
    Designation,
    Employee,
    EmployeeDocument,
    EmployeeHistory,
    HrHoliday,
    HrLeaveType,
    HrLetter,
    HrLetterTemplate,
    HrRequest,
    HrRequestEvent,
    HrSettings,
    HrWorkflow,
    HrWorkflowTask,
    PayrollPayslip,
    PayrollRun,
    StaffAttendance,
    StaffLeaveRequest,
)
from src.db.models.invitation import Invitation
from src.db.models.knowledge import KnowledgeChunk, KnowledgeDocument
from src.db.models.learning_session import LearningSession
from src.db.models.membership import SchoolMembership
from src.db.models.notification import Notification, NotificationPreference
from src.db.models.notification_delivery import NotificationDelivery
from src.db.models.admissions import AdmissionApplication, AdmissionDocument, AdmissionEnquiry, AdmissionStatusHistory
from src.db.models.parent import Parent
from src.db.models.platform_saas import PlatformAiSettings, PlatformAiUsageDaily, PlatformPlan, PlatformSettings
from src.db.models.platform_school_access import PlatformSchoolAccess
from src.db.models.progress import PracticeAttempt, Progress, StudentProgress
from src.db.models.question import Question
from src.db.models.refresh_token import RefreshToken
from src.db.models.reporting import AnalyticsSetting, ReportAuditLog, SavedReport, ScheduledReport
from src.db.models.result import Result
from src.db.models.school import School
from src.db.models.student import Student
from src.db.models.student_ai_memory import StudentAIMemory
from src.db.models.student_parent import StudentParent
from src.db.models.subject import Subject
from src.db.models.subscription import Subscription
from src.db.models.syllabus import Syllabus, SyllabusTopic
from src.db.models.teacher import Teacher
from src.db.models.teacher_assignment import TeacherAssignment, TeacherSubjectClass
from src.db.models.teaching_record import TeachingRecord
from src.db.models.timetable import Room, TimetableEntry, TimetablePeriod, TimetableSettings
from src.db.models.user import User

__all__ = [
    "User",
    "SchoolMembership",
    "Invitation",
    "RefreshToken",
    "AnalyticsSetting",
    "SavedReport",
    "ScheduledReport",
    "ReportAuditLog",
    "School",
    "Subscription",
    "PlatformPlan",
    "PlatformSettings",
    "PlatformAiSettings",
    "PlatformAiUsageDaily",
    "AcademicYear",
    "Grade",
    "ClassRoom",
    "Subject",
    "Teacher",
    "Student",
    "Parent",
    "PlatformSchoolAccess",
    "Enrollment",
    "StudentParent",
    "TeacherAssignment",
    "TeacherSubjectClass",
    "TimetableEntry",
    "TimetablePeriod",
    "TimetableSettings",
    "Room",
    "Syllabus",
    "SyllabusTopic",
    "TeachingRecord",
    "Homework",
    "HomeworkAttachment",
    "HomeworkSubmission",
    "SubmissionAttachment",
    "Attendance",
    "AttendanceAuditLog",
    "AttendanceSettings",
    "AttendanceDigestLog",
    "LeaveRequest",
    "StaffLeaveRequest",
    "StaffAttendance",
    "HrLetter",
    "EmployeeDocument",
    "HrWorkflow",
    "HrWorkflowTask",
    "HrRequest",
    "HrRequestEvent",
    "HrSettings",
    "HrHoliday",
    "HrLeaveType",
    "HrLetterTemplate",
    "EmployeeHistory",
    "Employee",
    "Department",
    "Designation",
    "Document",
    "DiaryEntry",
    "PersonalNote",
    "PersonalGoal",
    "PersonalGoalMilestone",
    "PersonalHabit",
    "PersonalHabitCheckin",
    "BookstoreItem",
    "BookstoreWishlist",
    "BookstoreOrder",
    "Exam",
    "Question",
    "ExamType",
    "ExamSection",
    "ExamSubject",
    "ExamSchedule",
    "MarksEntry",
    "StudentMark",
    "GradingScale",
    "GradeRule",
    "ResultAuditLog",
    "FeeCategory",
    "FeeStructure",
    "FeeStructureItem",
    "FeeInstallment",
    "StudentFeeAccount",
    "FeeAdjustment",
    "FeeInvoice",
    "FeeInvoiceItem",
    "Payment",
    "PaymentAllocation",
    "PaymentRefund",
    "FeeReceipt",
    "LateFeeRule",
    "FeeSettings",
    "FeeSequence",
    "FeeAuditLog",
    "FeeReminderLog",
    "Result",
    "StudentProgress",
    "PracticeAttempt",
    "Progress",
    "KnowledgeDocument",
    "KnowledgeChunk",
    "AIConversation",
    "AIMessage",
    "LearningSession",
    "StudentAIMemory",
    "Announcement",
    "Achievement",
    "AnnouncementAudience",
    "Message",
    "Conversation",
    "ConversationParticipant",
    "ConversationMessage",
    "CommunicationAuditLog",
    "Notification",
    "NotificationPreference",
    "NotificationDelivery",
    "PayrollRun",
    "PayrollPayslip",
    "AdmissionEnquiry",
    "AdmissionApplication",
    "AdmissionStatusHistory",
    "AdmissionDocument",
    "SecurityEvent",
]
