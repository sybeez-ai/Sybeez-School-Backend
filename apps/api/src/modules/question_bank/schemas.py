from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QuestionBankCreate(BaseModel):
    subject_id: UUID
    class_id: UUID | None = None
    prompt: str = Field(min_length=1, max_length=4000)
    question_type: str = "mcq"
    options: list[str] | None = None
    answer_key: str | None = None
    correct_index: int | None = Field(default=None, ge=0, le=10)
    points: int = Field(default=1, ge=1, le=100)


class QuestionBankUpdate(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    question_type: str | None = None
    options: list[str] | None = None
    answer_key: str | None = None
    correct_index: int | None = Field(default=None, ge=0, le=10)
    points: int | None = Field(default=None, ge=1, le=100)
    class_id: UUID | None = None


class QuestionBankOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    subject_id: UUID
    subject_name: str | None = None
    class_id: UUID | None = None
    class_name: str | None = None
    created_by: UUID | None = None
    author_name: str | None = None
    teacher_id: UUID | None = None
    prompt: str
    question_type: str
    options: list[str] | None = None
    answer_key: str | None = None
    correct_index: int | None = None
    points: int
    created_at: datetime
