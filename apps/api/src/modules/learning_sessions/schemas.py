from pydantic import BaseModel


class LearningSessionsStub(BaseModel):
    message: str = "learning_sessions module ready"
