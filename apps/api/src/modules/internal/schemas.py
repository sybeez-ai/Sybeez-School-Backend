from uuid import UUID

from pydantic import BaseModel, Field


class SubjectRef(BaseModel):
    id: UUID
    name: str


class CurrentLearning(BaseModel):
    subject_id: UUID | None = None
    topic: str | None = None
    teaching_record_id: UUID | None = None
    taught_content: list[str] = Field(default_factory=list)


class HomeworkRef(BaseModel):
    id: UUID
    title: str
    due_at: str | None = None


class StudentContext(BaseModel):
    id: UUID
    class_id: UUID | None = None


class SchoolContext(BaseModel):
    id: UUID


class AIContextResponse(BaseModel):
    student: StudentContext
    school: SchoolContext
    subjects: list[SubjectRef] = Field(default_factory=list)
    current_learning: CurrentLearning | None = None
    homework: list[HomeworkRef] = Field(default_factory=list)
