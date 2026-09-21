from pydantic import BaseModel


class QuestionsStub(BaseModel):
    message: str = "questions module ready"
