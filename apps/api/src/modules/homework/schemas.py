from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.shared.enums import HomeworkStatus, HomeworkSubmissionStatus, HomeworkType


class AttachmentOut(BaseModel):
    id: UUID
    file_name: str
    file_key: str
    mime_type: str | None = None
    file_size: int | None = None
    url: str | None = None

    model_config = {"from_attributes": True}


class HomeworkCreate(BaseModel):
    academic_year_id: UUID | None = None
    class_id: UUID  # section
    subject_id: UUID
    teacher_id: UUID | None = None  # admin may set; teachers use own profile
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    homework_type: HomeworkType = HomeworkType.HOMEWORK
    assigned_at: datetime | None = None
    due_at: datetime | None = None
    max_marks: float | None = Field(default=None, ge=0, le=1000)
    allow_resubmission: bool = True
    status: HomeworkStatus = HomeworkStatus.DRAFT
    syllabus_topic_id: UUID | None = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.assigned_at and self.due_at and self.due_at < self.assigned_at:
            raise ValueError("due_at must be on or after assigned_at")
        if self.status not in (HomeworkStatus.DRAFT, HomeworkStatus.PUBLISHED):
            raise ValueError("Create status must be DRAFT or PUBLISHED")
        return self


class HomeworkUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    homework_type: HomeworkType | None = None
    assigned_at: datetime | None = None
    due_at: datetime | None = None
    max_marks: float | None = Field(default=None, ge=0, le=1000)
    allow_resubmission: bool | None = None
    class_id: UUID | None = None
    subject_id: UUID | None = None
    academic_year_id: UUID | None = None
    syllabus_topic_id: UUID | None = None


class HomeworkOut(BaseModel):
    id: UUID
    school_id: UUID
    academic_year_id: UUID | None
    class_id: UUID
    class_name: str | None = None
    subject_id: UUID
    subject_name: str | None = None
    teacher_id: UUID
    teacher_name: str | None = None
    title: str
    description: str | None
    homework_type: str
    assigned_at: datetime | None
    due_at: datetime | None
    max_marks: float | None
    allow_resubmission: bool
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    attachments: list[AttachmentOut] = []
    # Aggregates / student view helpers
    submitted_count: int | None = None
    pending_count: int | None = None
    late_count: int | None = None
    graded_count: int | None = None
    total_students: int | None = None
    my_submission_status: str | None = None
    my_marks: float | None = None

    model_config = {"from_attributes": True}


class SubmissionCreate(BaseModel):
    content: str | None = Field(default=None, max_length=20000)


class SubmissionGrade(BaseModel):
    marks: float | None = Field(default=None, ge=0, le=1000)
    feedback: str | None = Field(default=None, max_length=5000)
    status: HomeworkSubmissionStatus | None = None  # GRADED or RETURNED

    @model_validator(mode="after")
    def validate_status(self):
        if self.status is not None and self.status not in (
            HomeworkSubmissionStatus.GRADED,
            HomeworkSubmissionStatus.RETURNED,
        ):
            raise ValueError("status must be GRADED or RETURNED")
        return self


class SubmissionOut(BaseModel):
    id: UUID | None = None
    homework_id: UUID
    student_id: UUID
    student_name: str | None = None
    school_id: UUID
    content: str | None
    submitted_at: datetime | None
    status: str
    marks: float | None
    feedback: str | None
    reviewed_by: UUID | None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None
    created_at: datetime | None = None
    attachments: list[AttachmentOut] = []
    homework_title: str | None = None
    max_marks: float | None = None

    model_config = {"from_attributes": True}


class SubmissionStatsOut(BaseModel):
    total: int
    submitted: int
    pending: int
    late: int
    graded: int
    returned: int


class ChildBriefOut(BaseModel):
    student_id: UUID
    full_name: str
    class_id: UUID | None = None
    class_name: str | None = None


class HomeworkReportOut(BaseModel):
    homework_id: UUID
    title: str
    class_name: str | None
    subject_name: str | None
    teacher_name: str | None
    status: str
    due_at: datetime | None
    total_students: int
    submitted: int
    pending: int
    late: int
    graded: int
