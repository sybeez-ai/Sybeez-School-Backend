from pydantic import BaseModel


class ExamsStub(BaseModel):
    message: str = "exams module ready"
