"""Shared status / domain enums for the school platform schema."""

from enum import StrEnum


class UserRole(StrEnum):
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    SCHOOL_ADMIN = "SCHOOL_ADMIN"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"
    PARENT = "PARENT"


class PlatformRole(StrEnum):
    """Sybeez company-side roles (platform console)."""

    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"  # legacy alias for PLATFORM_ADMIN operators
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    SUPPORT = "SUPPORT"
    BILLING = "BILLING"
    OPERATIONS = "OPERATIONS"
    SALES = "SALES"
    MARKETING = "MARKETING"
    ENGINEERING = "ENGINEERING"
    PRODUCT = "PRODUCT"
    FINANCE = "FINANCE"
    HR = "HR"
    ANALYST = "ANALYST"
    USER = "USER"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"
    LEFT = "LEFT"


class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class SchoolStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class SubscriptionStatus(StrEnum):
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELLED = "CANCELLED"


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"
    HALF_DAY = "HALF_DAY"


class LeaveRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PeriodKind(StrEnum):
    LESSON = "LESSON"
    BREAK = "BREAK"
    LUNCH = "LUNCH"


class HomeworkStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"


class HomeworkType(StrEnum):
    HOMEWORK = "HOMEWORK"
    ASSIGNMENT = "ASSIGNMENT"
    PROJECT = "PROJECT"
    WORKSHEET = "WORKSHEET"
    PRACTICE = "PRACTICE"


class HomeworkSubmissionStatus(StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    LATE = "LATE"
    GRADED = "GRADED"
    RETURNED = "RETURNED"


class PersonStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    GRADUATED = "GRADUATED"
    LEFT = "LEFT"


class EnrollmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    TRANSFERRED = "TRANSFERRED"
    WITHDRAWN = "WITHDRAWN"


class SubjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ExamStatus(StrEnum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    ONGOING = "ONGOING"
    MARKS_ENTRY = "MARKS_ENTRY"
    UNDER_REVIEW = "UNDER_REVIEW"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"


class ExamAttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    EXCUSED = "EXCUSED"


class MarkEntryStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    LOCKED = "LOCKED"


class KnowledgeSourceType(StrEnum):
    SYLLABUS = "SYLLABUS"
    TEACHING_RECORD = "TEACHING_RECORD"
    HOMEWORK = "HOMEWORK"
    TEXTBOOK = "TEXTBOOK"
    STUDY_MATERIAL = "STUDY_MATERIAL"
    SCHOOL_DOCUMENT = "SCHOOL_DOCUMENT"


class NotificationChannel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    PUSH = "PUSH"


class NotificationType(StrEnum):
    ATTENDANCE_ABSENCE = "ATTENDANCE_ABSENCE"
    ATTENDANCE_LATE = "ATTENDANCE_LATE"
    LEAVE_SUBMITTED = "LEAVE_SUBMITTED"
    LEAVE_APPROVED = "LEAVE_APPROVED"
    LEAVE_REJECTED = "LEAVE_REJECTED"
    HOMEWORK_PUBLISHED = "HOMEWORK_PUBLISHED"
    HOMEWORK_GRADED = "HOMEWORK_GRADED"
    HOMEWORK_RETURNED = "HOMEWORK_RETURNED"
    EXAM_PUBLISHED = "EXAM_PUBLISHED"
    RESULT_PUBLISHED = "RESULT_PUBLISHED"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    SYSTEM = "SYSTEM"
    FEE_INVOICE_ISSUED = "FEE_INVOICE_ISSUED"
    FEE_DUE_REMINDER = "FEE_DUE_REMINDER"
    FEE_OVERDUE = "FEE_OVERDUE"
    FEE_PAYMENT_SUCCESS = "FEE_PAYMENT_SUCCESS"
    FEE_PAYMENT_FAILED = "FEE_PAYMENT_FAILED"
    FEE_RECEIPT_READY = "FEE_RECEIPT_READY"
    FEE_REFUND = "FEE_REFUND"


class NotificationPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class AnnouncementStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class AudienceType(StrEnum):
    SCHOOL = "SCHOOL"
    ROLE = "ROLE"
    GRADE = "GRADE"
    SECTION = "SECTION"
    USER = "USER"


class ConversationType(StrEnum):
    PARENT_TEACHER = "PARENT_TEACHER"
    TEACHER_STUDENT = "TEACHER_STUDENT"
    DIRECT = "DIRECT"


class FeeChargeModel(StrEnum):
    ONE_TIME = "ONE_TIME"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    HALF_YEARLY = "HALF_YEARLY"
    YEARLY = "YEARLY"
    CUSTOM = "CUSTOM"


class FeeStructureStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class StudentFeeAccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    SUSPENDED = "SUSPENDED"


class FeeAdjustmentType(StrEnum):
    DISCOUNT = "DISCOUNT"
    SCHOLARSHIP = "SCHOLARSHIP"
    WAIVER = "WAIVER"
    ADJUSTMENT = "ADJUSTMENT"


class FeeInvoiceStatus(StrEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class PaymentMethod(StrEnum):
    ONLINE = "ONLINE"
    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    CHEQUE = "CHEQUE"
    UPI = "UPI"
    CARD = "CARD"
    OTHER = "OTHER"


class LateFeeCalcType(StrEnum):
    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"
    PER_DAY = "PER_DAY"


class RefundStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
