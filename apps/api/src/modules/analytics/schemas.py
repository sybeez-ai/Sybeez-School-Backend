from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsFilters(BaseModel):
    academic_year_id: UUID | None = None
    grade_id: UUID | None = None
    section_id: UUID | None = None
    subject_id: UUID | None = None
    exam_id: UUID | None = None
    from_date: date | None = None
    to_date: date | None = None


class MetricPoint(BaseModel):
    label: str
    value: float
    meta: dict | None = None


class AttentionItem(BaseModel):
    student_id: UUID
    student_name: str
    class_name: str | None = None
    indicator: str
    detail: str
    value: float | None = None


class OverviewOut(BaseModel):
    students: int
    teachers: int
    attendance_rate: float
    homework_completion: float
    exam_average: float
    fees_collection_rate: float
    attendance_trend: list[MetricPoint] = []
    exam_trend: list[MetricPoint] = []
    fee_trend: list[MetricPoint] = []
    homework_trend: list[MetricPoint] = []
    attention: list[AttentionItem] = []
    currency: str = "INR"
    updated_at: datetime


class AcademicOut(BaseModel):
    total_students: int
    active_students: int
    teachers: int
    subjects: int
    by_grade: list[MetricPoint] = []
    by_section: list[MetricPoint] = []


class StudentsAnalyticsOut(BaseModel):
    total: int
    active: int
    inactive: int
    transferred: int
    new_admissions: int
    withdrawals: int
    by_grade: list[MetricPoint] = []


class AttendanceAnalyticsOut(BaseModel):
    attendance_rate: float
    present: int
    absent: int
    late: int
    excused: int
    half_day: int
    total: int
    trend: list[MetricPoint] = []
    by_grade: list[MetricPoint] = []
    by_section: list[MetricPoint] = []
    needs_attention: list[AttentionItem] = []


class HomeworkAnalyticsOut(BaseModel):
    total_assignments: int
    published: int
    submissions: int
    late: int
    missing: int
    graded: int
    submission_rate: float
    on_time_rate: float
    late_rate: float
    missing_rate: float
    by_class: list[MetricPoint] = []
    by_subject: list[MetricPoint] = []


class ExamAnalyticsOut(BaseModel):
    students: int
    average: float | None
    median: float | None
    highest: float | None
    lowest: float | None
    pass_rate: float | None
    fail_rate: float | None
    by_subject: list[MetricPoint] = []
    exam_trend: list[MetricPoint] = []


class FeesAnalyticsOut(BaseModel):
    currency: str
    assessed: float
    collected: float
    outstanding: float
    overdue: float
    collection_rate: float
    trend: list[MetricPoint] = []
    by_grade: list[MetricPoint] = []


class CommunicationAnalyticsOut(BaseModel):
    announcements_sent: int
    notifications_sent: int
    notifications_read: int
    notifications_unread: int
    messages_sent: int
    unread_messages: int


class ClassAnalyticsOut(BaseModel):
    section_id: UUID
    class_name: str
    students: int
    attendance_rate: float
    homework_completion: float
    exam_average: float | None
    fee_collection_rate: float | None


class StudentAnalyticsOut(BaseModel):
    student_id: UUID
    student_name: str
    class_name: str | None = None
    attendance_rate: float | None
    homework_completion: float | None
    latest_exam_percentage: float | None
    fees_outstanding: float | None
    exam_trend: list[MetricPoint] = []
    subject_scores: list[MetricPoint] = []
    attendance_trend: list[MetricPoint] = []


class TeacherAnalyticsOut(BaseModel):
    classes: list[ClassAnalyticsOut] = []
    assignments_created: int = 0
    assignments_graded: int = 0
    attendance_marked_days: int = 0
    students_taught: int = 0


class AnalyticsSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attendance_threshold: float
    missing_homework_threshold: int
    low_exam_percentage_threshold: float


class AnalyticsSettingsUpdate(BaseModel):
    attendance_threshold: float | None = Field(default=None, ge=0, le=100)
    missing_homework_threshold: int | None = Field(default=None, ge=0)
    low_exam_percentage_threshold: float | None = Field(default=None, ge=0, le=100)
