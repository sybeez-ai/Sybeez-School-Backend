from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.shared.enums import HomeworkStatus


class HomeworkCreate(BaseModel):
    class_id: UUID
    subject_id: UUID
    teacher_id: UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    due_at: datetime | None = None
    status: HomeworkStatus = HomeworkStatus.PUBLISHED
    syllabus_topic_id: UUID | None = None


class HomeworkUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    due_at: datetime | None = None
    status: HomeworkStatus | None = None


class HomeworkOut(BaseModel):
    id: UUID
    school_id: UUID
    class_id: UUID
    subject_id: UUID
    teacher_id: UUID
    title: str
    description: str | None
    due_at: datetime | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SubmissionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50000)


class SubmissionGrade(BaseModel):
    marks: float = Field(ge=0, le=1000)
    feedback: str | None = Field(default=None, max_length=5000)


class SubmissionOut(BaseModel):
    id: UUID
    homework_id: UUID
    student_id: UUID
    school_id: UUID
    content: str | None
    status: str
    marks: float | None
    feedback: str | None
    submitted_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
