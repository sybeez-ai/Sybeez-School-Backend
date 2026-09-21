from uuid import UUID

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    school_id: UUID
    student_id: UUID | None = None
    class_id: UUID | None = None
    subject_id: UUID | None = None
    teaching_record_id: UUID | None = None
    conversation_id: UUID | None = None
    message: str = Field(min_length=1)
    mode: str = "board"  # board | 3d | picture


class ChatResponse(BaseModel):
    conversation_id: UUID | None = None
    reply: str
    sources: list[str] = []
